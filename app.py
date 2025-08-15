# app.py
from flask import Flask, render_template, request, send_file, jsonify
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_LINE_SPACING
import os, re, logging, json, hashlib, fnmatch
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from copy import deepcopy
from openai import OpenAI

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dsv-app")

# ---------------- AI / RAG config ----------------
AI_MODEL = os.getenv("AI_CHAT_MODEL", "gpt-4o-mini")
EMB_MODEL = os.getenv("AI_EMB_MODEL", "text-embedding-3-small")
RAG_INDEX_PATH = Path("rag_index.npz")
RAG_META_PATH  = Path("rag_index_meta.json")

RAG_FOLDERS = ["templates", "templates/chatbot", "static"]  # explicit chatbot
RAG_GLOBS = ["*.py","*.js","*.ts","*.html","*.css","*.txt","*.md","*.docx","*.xlsx","*.pdf"]
EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", "generated"}

CHARS_PER_CHUNK   = 1200
CHUNK_OVERLAP     = 200
TOP_K             = 12
MAX_CONTEXT_CHARS = 16000
MAX_FILE_BYTES    = 1_500_000
MAX_TOTAL_CHUNKS  = 4000
EMBED_BATCH       = 32
AI_DEBUG          = os.getenv("AI_DEBUG", "0") == "1"

# pre-init to avoid NameError
RAG_VECTORS = np.zeros((0, 1536), dtype=np.float32)
RAG_META = []

# ---------------- Flask ----------------
app = Flask(__name__)

# ================== Matrix / pricing helpers (unchanged structure) ==================
TARIFF_PATH = "CL TARIFF - 2025 v3 (004) - UPDATED 6TH AUGUST 2025.xlsx"

def _is_num(x):
    return isinstance(x, (int, float)) and pd.notna(x)

def _daily_rate_from_row(df, r, from_col=2, to_col=3):
    candidates, fallback = [], []
    for c in range(df.shape[1]):
        if c in (from_col, to_col):
            continue
        v = df.iat[r, c]
        if _is_num(v):
            v = float(v)
            if 1.2 <= v <= 10.0:
                candidates.append(v)
            elif v > 0:
                fallback.append(v)
    if candidates:
        return min(candidates)
    return min(fallback) if fallback else 0.0

def _bands_from_rows(df, rows, from_col=2, to_col=3):
    out = []
    for r in rows:
        f = df.iat[r, from_col]
        t = df.iat[r, to_col]
        if isinstance(t, str) and "above" in t.lower():
            t = float("inf")
        out.append((float(f), float(t), float(_daily_rate_from_row(df, r, from_col, to_col))))
    return out

def load_matrix():
    xls = pd.ExcelFile(TARIFF_PATH)
    wh = pd.read_excel(xls, "CL - WH & OY (2)", header=None)
    ac_lt1m_rows  = [5, 6, 7]
    ac_ge1m_rows  = [12, 13, 14]
    dry_lt1m_rows = [22, 23, 24]
    dry_ge1m_rows = [29, 30, 31]
    return {
        "ac":  {"lt1m": _bands_from_rows(wh, ac_lt1m_rows),  "ge1m": _bands_from_rows(wh, ac_ge1m_rows)},
        "dry": {"lt1m": _bands_from_rows(wh, dry_lt1m_rows), "ge1m": _bands_from_rows(wh, dry_ge1m_rows)},
    }

MATRIX = load_matrix()

def _pick_rate(bands, volume):
    for f, t, r in bands:
        if f <= volume <= t:
            return r
    return bands[-1][2] if bands else 0.0

@app.route("/")
def index():
    return render_template("form.html")

def compute_item(storage_type, volume, days, include_wms, commodity=""):
    st = (storage_type or "").strip()
    st_lower = st.lower()
    period_lt_1m = days < 30

    rate = 0.0
    unit, rate_unit = "CBM", "CBM / DAY"
    storage_fee = 0.0

    if st in ("AC", "Non-AC", "Open Shed"):
        family = "ac" if st == "AC" else "dry"
        bands = MATRIX[family]["lt1m"] if period_lt_1m else MATRIX[family]["ge1m"]
        rate = _pick_rate(bands, volume)
        storage_fee = volume * days * rate

    elif st == "Chemicals AC":
        rate = 3.5; storage_fee = volume * days * rate
    elif st == "Chemicals Non-AC (Non-DG)":
        rate = 2.5; storage_fee = volume * days * rate
    elif st == "Chemicals Non-AC (DG)":
        rate = 3.0; storage_fee = volume * days * rate
    elif st == "Chemicals Non-AC":
        rate = 2.5; storage_fee = volume * days * rate

    elif "open yard – kizad" in st_lower:
        rate, unit, rate_unit = 125.0, "SQM", "SQM / YEAR"
        storage_fee = volume * days * (rate / 365.0)
    elif st == "Open Yard – Mussafah (Open Yard)":
        unit, rate_unit, rate = "SQM", "SQM / MONTH", 15.0
        storage_fee = volume * days * (rate / 30.0)
    elif st == "Open Yard – Mussafah (Open Yard Shed)":
        unit, rate_unit, rate = "SQM", "SQM / MONTH", 35.0
        storage_fee = volume * days * (rate / 30.0)
    elif st == "Open Yard – Mussafah (Jumbo Bag)":
        unit, rate_unit, rate = "BAG", "BAG / MONTH", 19.0
        storage_fee = volume * days * (rate / 30.0)

    elif st.startswith("RMS — Premium"):
        unit, rate_unit, rate = "BOX", "BOX / MONTH", 5.0
        storage_fee = volume * days * (rate / 30.0)
    elif st.startswith("RMS — Normal"):
        unit, rate_unit, rate = "BOX", "BOX / MONTH", 3.0
        storage_fee = volume * days * (rate / 30.0)

    is_open_yard = "open yard" in st_lower
    is_chemical  = "chemical"  in st_lower
    is_rms       = st_lower.startswith("rms")
    months = max(1, days // 30)
    wms_monthly = 625 if is_chemical else 1500
    wms_fee = 0 if is_open_yard or not include_wms else wms_monthly * months

    return {
        "storage_type": st, "unit": unit, "rate": float(rate), "rate_unit": rate_unit,
        "volume": float(volume), "days": int(days), "storage_fee": round(storage_fee, 2),
        "include_wms": bool(include_wms and not is_open_yard), "wms_monthly": wms_monthly,
        "months": months, "wms_fee": int(0 if is_open_yard or not include_wms else wms_monthly * months),
        "category_standard": st in ("AC", "Non-AC", "Open Shed"),
        "category_chemical": is_chemical, "category_openyard": is_open_yard,
        "category_rms": is_rms,
        "commodity": (commodity or "").strip(),
    }

def _clean_commodity(val: str) -> str:
    s = (val or "").strip()
    if not s: return ""
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return ""
    return s

def find_quote_table(doc):
    for t in doc.tables:
        try:
            head = " ".join(c.text.strip().lower() for c in t.rows[0].cells)
        except Exception:
            continue
        if "item" in head and "unit rate" in head and "amount" in head:
            return t
    return None

def _delete_row(table, row_idx):
    row = table.rows[row_idx]
    row._element.getparent().remove(row._element)

def _clear_quote_table_keep_header(table):
    while len(table.rows) > 1:
        _delete_row(table, 1)

def _body_children(doc):
    return list(doc._element.body.iterchildren())

def _el_text(el) -> str:
    return "".join(el.itertext())

def _copy_blocks_between_markers(src_doc, start_tag, end_tag):
    body = _body_children(src_doc)
    start_i = end_i = None
    for i, el in enumerate(body):
        if el.tag.endswith('p') and start_tag in _el_text(el):
            start_i = i
        if start_i is not None and el.tag.endswith('p') and end_tag in _el_text(el):
            end_i = i; break
    if start_i is None or end_i is None or end_i <= start_i + 1:
        return []
    return [deepcopy(el) for el in body[start_i + 1:end_i]]

def _append_blocks(dest_doc, blocks):
    body = dest_doc._element.body
    for el in blocks:
        body.append(el)

def _is_vas_table(tbl):
    if not tbl.rows: return False
    hdr = " | ".join(c.text.strip().lower() for c in tbl.rows[0].cells)
    return ("vas" in hdr and "price" in hdr) or ("main activity" in hdr and "transaction" in hdr)

def _purge_all_vas_tables_and_headings(doc):
    for p in list(doc.paragraphs):
        t = (p.text or "").strip()
        if t in ("Standard VAS", "Chemical VAS", "Open Yard VAS",
                 "Terms & Conditions — Chemical", "Terms & Conditions — Open Yard",
                 "Value Added Service Rates (Standard VAS)", "Value Added Service Rates",
                 "RMS VAS", "Terms & Conditions — RMS"):
            el = p._element; pa = el.getparent()
            if pa is not None: pa.remove(el)
    for tbl in list(doc.tables):
        if _is_vas_table(tbl):
            el = tbl._element; pa = el.getparent()
            if pa is not None: pa.remove(el)

def _safe_docx(path: str):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return Document(str(p))
    except Exception:
        return None

def _vas_blocks_from_template(path, start_tag, end_tag, heading):
    src = _safe_docx(path)
    if not src:
        return []
    blocks = _copy_blocks_between_markers(src, start_tag, end_tag)
    if blocks:
        tmp = Document(); hp = tmp.add_paragraph(heading); hp.runs[0].bold = True
        return [deepcopy(hp._element)] + blocks
    for tbl in src.tables:
        if _is_vas_table(tbl):
            tmp = Document(); hp = tmp.add_paragraph(heading); hp.runs[0].bold = True
            return [deepcopy(hp._element), deepcopy(tbl._element)]
    return []

def _find_para_el_contains(doc, needle):
    for p in doc.paragraphs:
        if needle.lower() in (p.text or "").lower():
            return p._element
    return None

def _move_element_after(doc, moving_el, after_el):
    if moving_el is None or after_el is None: return
    body = doc._element.body
    par = moving_el.getparent()
    if par is not None: par.remove(moving_el)
    idx = body.index(after_el)
    body.insert(idx + 1, moving_el)

def _set_table_line_spacing(table, rule=WD_LINE_SPACING.ONE_POINT_FIVE):
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.line_spacing_rule = rule

def _rebuild_quotation_table(doc, items, grand_total):
    qt = find_quote_table(doc)
    if not qt:
        doc.add_paragraph()
        qt = doc.add_table(rows=1, cols=3)
        hdr = qt.rows[0].cells
        hdr[0].text, hdr[1].text, hdr[2].text = "Item", "Unit Rate", "Amount (AED)"
    _clear_quote_table_keep_header(qt)

    for it in items:
        r = qt.add_row().cells
        r[0].text = it['storage_type']
        r[1].text = f"{it['rate']:.2f} AED / {it['rate_unit']}"
        r[2].text = f"{it['storage_fee']:,.2f} AED"
        if it["include_wms"]:
            r = qt.add_row().cells
            r[0].text = f"WMS — {it['storage_type']} ({it['months']} month{'s' if it['months']!=1 else ''})"
            r[1].text = f"{it['wms_monthly']:.2f} AED / MONTH"
            r[2].text = f"{it['wms_fee']:,.2f} AED"

    total_row = qt.add_row().cells
    total_row[0].text = "Total Fee"; total_row[1].text = ""; total_row[2].text = f"{grand_total:,.2f} AED"
    for p in total_row[0].paragraphs:
        for run in p.runs: run.bold = True; run.font.size = Pt(11)
    for p in total_row[2].paragraphs:
        for run in p.runs: run.bold = True; run.font.size = Pt(11)
    _set_table_line_spacing(qt, WD_LINE_SPACING.ONE_POINT_FIVE)
    return qt

def _delete_summary_lines_for_multi(doc):
    targets = ("Storage Period:", "Storage Size:",
               "We trust that the rates", "Yours Faithfully", "DSV Solutions PJSC", "Validity:")
    to_remove = []
    for p in doc.paragraphs:
        t = (p.text or "").strip().lower()
        if any(t.startswith(x.lower()) for x in targets) or "value added service rates" in t:
            to_remove.append(p)
    for p in set(to_remove):
        el = p._element; pa = el.getparent()
        if pa is not None: pa.remove(el)

def _append_terms_from_template(path,
                                terms_heading="Storage Terms and Conditions:",
                                end_markers=("Validity:", "We trust that")):
    src = _safe_docx(path)
    if not src:
        return []
    body = _body_children(src)
    start_i = None
    for i, el in enumerate(body):
        if el.tag.endswith("p") and terms_heading.lower() in _el_text(el).lower():
            start_i = i; break
    if start_i is None: return []
    end_i = len(body)
    for j in range(start_i + 1, len(body)):
        if body[j].tag.endswith("p"):
            txt = _el_text(body[j]).strip()
            if any(m.lower() in txt.lower() for m in end_markers):
                end_i = j; break
    return [deepcopy(el) for el in body[start_i + 1:end_i]]

def _remove_base_terms(doc, terms_heading="Storage Terms and Conditions:"):
    body = _body_children(doc)
    start_i = None
    for i, el in enumerate(body):
        if el.tag.endswith('p') and terms_heading.lower() in _el_text(el).lower():
            start_i = i; break
    if start_i is None: return
    for j in range(len(body) - 1, start_i - 1, -1):
        parent = body[j].getparent()
        if parent is not None:
            parent.remove(body[j])

def _strip_marker_text(doc):
    tags = ["[VAS_STANDARD]", "[/VAS_STANDARD]",
            "[VAS_CHEMICAL]", "[/VAS_CHEMICAL]",
            "[VAS_OPENYARD]", "[/VAS_OPENYARD]",
            "[VAS_RMS]", "[/VAS_RMS]"]
    for p in doc.paragraphs:
        t = p.text or ""; new = t
        for tg in tags: new = new.replace(tg, "")
        if new != t:
            for r in p.runs: r.text = ""
            p.add_run(new)
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    t = p.text or ""; new = t
                    for tg in tags: new = new.replace(tg, "")
                    if new != t:
                        for r in p.runs: r.text = ""
                        p.add_run(new)

# ---------------- ROUTE: generate ----------------
@app.route("/generate", methods=["POST"])
def generate():
    storage_types = request.form.getlist("storage_type") or [request.form.get("storage_type", "")]
    volumes       = request.form.getlist("volume")       or [request.form.get("volume", 0)]
    days_list     = request.form.getlist("days")         or [request.form.get("days", 0)]
    wms_list      = request.form.getlist("wms")          or [request.form.get("wms", "No")]
    commodities_raw = request.form.getlist("commodity") or [request.form.get("commodity", "")]
    canonical_commodity = next((c for c in (_clean_commodity(v) for v in commodities_raw) if c), "")

    rms_tier_list  = request.form.getlist("rms_tier")  or []
    rms_boxes_list = request.form.getlist("rms_boxes") or []

    n = max(len(storage_types), len(volumes), len(days_list), len(wms_list))
    storage_types += [""] * (n - len(storage_types))
    volumes       += [0]  * (n - len(volumes))
    days_list     += [0]  * (n - len(days_list))
    wms_list      += ["No"] * (n - len(wms_list))
    if len(rms_tier_list)  < n: rms_tier_list  += [""] * (n - len(rms_tier_list))
    if len(rms_boxes_list) < n: rms_boxes_list += ["0"] * (n - len(rms_boxes_list))

    items = []
    for i in range(n):
        raw_st = storage_types[i]
        vol = float(volumes[i] or 0)
        d   = int(days_list[i] or 0)
        inc = (wms_list[i] == "Yes")
        com = _clean_commodity(commodities_raw[i] if i < len(commodities_raw) else "") or canonical_commodity

        st_lower = (raw_st or "").strip().lower()
        if st_lower == "rms":
            tier = (rms_tier_list[i] or "").strip()
            st_label = ("RMS — Premium FM200 Archiving AC Facility"
                        if "premium" in tier.lower() else "RMS — Normal AC Facility")
            vol = float(rms_boxes_list[i] or 0)
            inc = True
            items.append(compute_item(st_label, vol, d, inc, com))
            continue

        if "open yard" in st_lower:
            inc = False

        if not raw_st:
            continue
        items.append(compute_item(raw_st, vol, d, inc, com))

    if len(items) > 1:
        has_rms  = any(it.get("category_rms") for it in items)
        has_std  = any(it.get("category_standard") for it in items)
        has_chem = any(it.get("category_chemical") for it in items)

        def _max_months(filter_fn):
            vals = [it["months"] for it in items if filter_fn(it)]
            return max(vals) if vals else 0

        if has_rms and has_std and not has_chem:
            months_agg = max(
                _max_months(lambda x: x.get("category_rms")),
                _max_months(lambda x: x.get("category_standard") and x.get("include_wms"))
            ) or _max_months(lambda x: x.get("category_rms")) or 1
            for it in items:
                if it.get("category_rms") or (it.get("category_standard") and it.get("include_wms")):
                    it["wms_fee"] = 0
                    it["include_wms"] = False
            carrier = next((it for it in items if it.get("category_rms")), items[0])
            carrier["include_wms"] = True
            carrier["wms_monthly"] = 1500
            carrier["months"] = months_agg
            carrier["wms_fee"] = 1500 * months_agg

        elif has_rms and has_chem:
            rms_months  = _max_months(lambda x: x.get("category_rms")) or 1
            chem_months = _max_months(lambda x: x.get("category_chemical")) or 1
            for it in items:
                if it.get("category_rms") or it.get("category_chemical"):
                    it["wms_fee"] = 0
                    it["include_wms"] = False
            rms_carrier = next((it for it in items if it.get("category_rms")), items[0])
            rms_carrier["include_wms"] = True
            rms_carrier["wms_monthly"] = 1500
            rms_carrier["months"] = rms_months
            rms_carrier["wms_fee"] = 1500 * rms_months
            chem_carrier = next((it for it in items if it.get("category_chemical")), None)
            if chem_carrier:
                chem_carrier["include_wms"] = True
                chem_carrier["wms_monthly"] = 625
                chem_carrier["months"] = chem_months
                chem_carrier["wms_fee"] = 625 * chem_months

    if not items:
        items = [compute_item("AC", 0.0, 0, False)]

    today_str = datetime.today().strftime("%d %b %Y")

    if len(items) == 1:
        st0 = items[0]["storage_type"].lower()
        if "chemical" in st0: template_path = "templates/Chemical VAS.docx"
        elif "open yard" in st0: template_path = "templates/Open Yard VAS.docx"
        elif st0.startswith("rms"):
            template_path = "templates/RMS VAS.docx" if _safe_docx("templates/RMS VAS.docx") else "templates/Standard VAS.docx"
        else: template_path = "templates/Standard VAS.docx"
    else:
        template_path = "templates/Standard VAS.docx"
    doc = Document(template_path)

    total_storage = round(sum(i["storage_fee"] for i in items), 2)
    total_wms     = round(sum(i["wms_fee"]    for i in items), 2)
    grand_total   = round(total_storage + total_wms, 2)

    if len(items) == 1:
        one = items[0]
        unit_for_display = one["unit"]; unit_rate_text = f"{one['rate']:.2f} AED / {one['rate_unit']}"
        wms_status = "" if one["category_openyard"] else ("INCLUDED" if one["include_wms"] else "NOT INCLUDED")
        storage_type_text = one["storage_type"]; days_text = str(one["days"]); volume_text = str(one["volume"])
        commodity_text = one.get("commodity") or _clean_commodity(request.form.get("commodity")) or "N/A"
    else:
        st_lines = []
        for it in items:
            qty = f"{it['volume']} {it['unit']}".strip()
            dur = f"{it['days']} day{'s' if it['days'] != 1 else ''}"
            st_lines.append(f"{it['storage_type']} ({qty} × {dur})")
        storage_type_text = "\n".join(st_lines)
        cm_lines = []
        for it in items:
            cm = it.get("commodity") or _clean_commodity(request.form.get("commodity")) or "N/A"
            cm_lines.append(f"{it['storage_type']} — {cm}")
        commodity_text = "\n".join(cm_lines)
        unit_for_display = ""; unit_rate_text = "—"
        days_text = "VARIOUS"; volume_text = "VARIOUS"
        wms_status = "SEE BREAKDOWN"

    placeholders = {
        "{{STORAGE_TYPE}}": storage_type_text, "{{DAYS}}": days_text, "{{VOLUME}}": volume_text,
        "{{UNIT}}": unit_for_display, "{{WMS_STATUS}}": wms_status, "{{UNIT_RATE}}": unit_rate_text,
        "{{STORAGE_FEE}}": f"{total_storage:,.2f} AED", "{{WMS_FEE}}": f"{total_wms:,.2f} AED",
        "{{TOTAL_FEE}}": f"{grand_total:,.2f} AED", "{{TODAY_DATE}}": today_str,
        "{{COMMODITY}}": commodity_text,
        "1,500.00 AED / MONTH": "625.00 AED / MONTH" if any(i["category_chemical"] for i in items) else "1,500.00 AED / MONTH",
    }

    def replace_in_paragraph(paragraph, mapping):
        if not paragraph.runs: return
        full = "".join(r.text for r in paragraph.runs); new = full
        for k, v in mapping.items(): new = new.replace(k, v)
        if new != full:
            for r in paragraph.runs: r.text = ""
            paragraph.runs[0].text = new

    def replace_all(doc_obj, mapping):
        for p in doc_obj.paragraphs: replace_in_paragraph(p, mapping)
        for table in doc_obj.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs: replace_in_paragraph(p, mapping)

    replace_all(doc, placeholders)

    if len(items) > 1:
        _delete_summary_lines_for_multi(doc)

    qt = _rebuild_quotation_table(doc, items, grand_total)

    used_standard = any(i["category_standard"] for i in items)
    used_chemical = any(i["category_chemical"] for i in items)
    used_openyard = any(i["category_openyard"] for i in items)
    used_rms      = any(i.get("category_rms") for i in items)

    if len(items) > 1:
        _purge_all_vas_tables_and_headings(doc)

        fam_order = []
        for it in items:
            fam = ("rms" if it.get("category_rms") else
                   "standard" if it["category_standard"] else
                   "chemical" if it["category_chemical"] else
                   "openyard" if it["category_openyard"] else None)
            if fam and fam not in fam_order: fam_order.append(fam)

        family_blocks = []
        for fam in fam_order:
            if fam == "standard" and used_standard:
                family_blocks += _vas_blocks_from_template("templates/Standard VAS.docx", "[VAS_STANDARD]", "[/VAS_STANDARD]", "Standard VAS")
            elif fam == "chemical" and used_chemical:
                family_blocks += _vas_blocks_from_template("templates/Chemical VAS.docx", "[VAS_CHEMICAL]", "[/VAS_CHEMICAL]", "Chemical VAS")
            elif fam == "openyard" and used_openyard:
                family_blocks += _vas_blocks_from_template("templates/Open Yard VAS.docx", "[VAS_OPENYARD]", "[/VAS_OPENYARD]", "Open Yard VAS")
            elif fam == "rms" and used_rms:
                family_blocks += _vas_blocks_from_template("templates/RMS VAS.docx", "[VAS_RMS]", "[/VAS_RMS]", "RMS VAS")

        note_el = _find_para_el_contains(doc, "Minimum Monthly storage charges")
        anchor = note_el if (note_el is not None and qt is not None) else (qt._element if qt is not None else None)
        if note_el is not None and qt is not None:
            _move_element_after(doc, note_el, qt._element)
        if anchor is not None and family_blocks:
            idx = doc._element.body.index(anchor)
            for i, el in enumerate(family_blocks):
                doc._element.body.insert(idx + 1 + i, el)
        else:
            _append_blocks(doc, family_blocks)

        _remove_base_terms(doc)

        combined = []
        if used_standard: combined += _append_terms_from_template("templates/Standard VAS.docx")
        if used_chemical: combined += _append_terms_from_template("templates/Chemical VAS.docx")
        if used_openyard: combined += _append_terms_from_template("templates/Open Yard VAS.docx")
        if used_rms:      combined += _append_terms_from_template("templates/RMS VAS.docx")

        doc.add_paragraph(" ")
        h = doc.add_paragraph("Terms & Conditions — Combined"); h.runs[0].bold = True

        seen = set()
        non_haz_line = "The above rates offered are for non-Haz cargo."
        keep_non_haz = not any(it["storage_type"] == "Chemicals AC" for it in items)

        for el in combined:
            if not el.tag.endswith('p'):
                continue
            raw = _el_text(el).strip()
            if not raw:
                continue
            norm = re.sub(r"\s+", " ", raw).strip().lower()
            if not keep_non_haz and non_haz_line.lower() in norm:
                continue
            if norm in seen:
                continue
            seen.add(norm)
            doc._element.body.append(deepcopy(el))

        if keep_non_haz and non_haz_line.lower() not in seen:
            doc.add_paragraph(non_haz_line)

        doc.add_paragraph(" ")
        doc.add_paragraph("Validity: 15 Days")
        doc.add_paragraph("")
        doc.add_paragraph("We trust that the rates and services provided are to your satisfaction and should you require any further details please do not hesitate to contact me.")
        doc.add_paragraph("Yours Faithfully,")
        doc.add_paragraph("")
        doc.add_paragraph("DSV Solutions PJSC")

    _strip_marker_text(doc)

    os.makedirs("generated", exist_ok=True)
    filename = f"Quotation_{(request.form.get('commodity') or 'quotation').strip() or 'quotation'}.docx"
    output_path = os.path.join("generated", filename)
    doc.save(output_path)
    return send_file(output_path, as_attachment=True)

# ================== RAG index build ==================
def _list_files_for_rag():
    files = []
    for folder in RAG_FOLDERS:
        base = Path(folder)
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            if not any(fnmatch.fnmatch(p.name, g) for g in RAG_GLOBS):
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except Exception:
                pass
            files.append(p)
    seen, uniq = set(), []
    for p in files:
        k = str(p.resolve())
        if k not in seen:
            seen.add(k); uniq.append(p)
    return uniq

def _read_pdf_file(p: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(p))
        out = []
        for i, page in enumerate(reader.pages[:120]):
            txt = page.extract_text() or ""
            txt = txt.strip()
            if txt:
                out.append(txt)
        return "\n".join(out)
    except Exception:
        return ""

def _file_text_for_rag(p: Path) -> str:
    suf = p.suffix.lower()
    if suf in [".txt",".md",".py",".js",".ts",".html",".css"]:
        try: return p.read_text(encoding="utf-8", errors="ignore")
        except: return ""
    if suf == ".docx":
        try:
            d=Document(str(p))
            parts=[]
            for para in d.paragraphs:
                if para.text.strip(): parts.append(para.text.strip())
            for tbl in d.tables:
                for row in tbl.rows:
                    cells=[c.text.strip() for c in row.cells]
                    if any(cells): parts.append(" | ".join(cells))
            return "\n".join(parts)
        except: return ""
    if suf == ".xlsx":
        try:
            xls=pd.ExcelFile(str(p))
            out=[]
            for sh in xls.sheet_names:
                df=pd.read_excel(xls,sheet_name=sh,header=None).astype(str)
                out.append(f"### Sheet: {sh}")
                out.append(df.head(200).to_csv(index=False,header=False))
            return "\n".join(out)
        except: return ""
    if suf == ".pdf":
        return _read_pdf_file(p)
    return ""

def _build_or_load_index_rag():
    if not os.getenv("OPENAI_API_KEY"):
        return np.zeros((0,1536),dtype=np.float32), []
    client=OpenAI()
    files=_list_files_for_rag()
    sig=[]
    for p in files:
        try: sig.append(f"{p}:{int(p.stat().st_mtime)}")
        except: pass
    signature=hashlib.md5("|".join(sorted(sig)).encode("utf-8")).hexdigest()
    if RAG_INDEX_PATH.exists() and RAG_META_PATH.exists():
        try:
            saved=json.loads(RAG_META_PATH.read_text())
            if saved.get("signature")==signature:
                vecs=np.load(RAG_INDEX_PATH)["vectors"]
                return vecs, saved["meta"]
        except: pass
    meta=[]; chunks=[]
    for p in files:
        text=_file_text_for_rag(p)
        if not text: continue
        i, n = 0, len(text)
        while i < n and len(chunks) < MAX_TOTAL_CHUNKS:
            j = min(n, i + CHARS_PER_CHUNK)
            chunk_text = text[i:j]
            chunks.append(chunk_text); meta.append({"path": str(p), "text": chunk_text})
            if j == n: break
            i = max(0, j - CHUNK_OVERLAP)
        if len(chunks) >= MAX_TOTAL_CHUNKS: break
    if not chunks:
        RAG_META_PATH.write_text(json.dumps({"signature":signature,"meta":[]}))
        np.savez_compressed(RAG_INDEX_PATH, vectors=np.zeros((0,1536),dtype=np.float32))
        return np.zeros((0,1536),dtype=np.float32), []
    vecs=[]
    for i in range(0,len(chunks),EMBED_BATCH):
        emb=client.embeddings.create(model=EMB_MODEL, input=chunks[i:i+EMBED_BATCH])
        vecs.extend([e.embedding for e in emb.data])
    vecs=np.array(vecs,dtype=np.float32)
    np.savez_compressed(RAG_INDEX_PATH, vectors=vecs)
    RAG_META_PATH.write_text(json.dumps({"signature":signature,"meta":meta}))
    return vecs, meta

def _ensure_index():
    """Force rebuild of RAG index every startup."""
    global RAG_VECTORS, RAG_META
    # always remove old cache
    try: os.remove(RAG_INDEX_PATH)
    except FileNotFoundError: pass
    try: os.remove(RAG_META_PATH)
    except FileNotFoundError: pass
    RAG_VECTORS, RAG_META = _build_or_load_index_rag()
    log.info("RAG index rebuilt with %d chunks from %d paths",
             RAG_VECTORS.shape[0], len({m['path'] for m in RAG_META}))

# ================== Hybrid retrieval & chat ==================
def _tokenize(s: str):
    return re.findall(r"[a-z0-9]+", (s or "").lower())

def _retrieve_ctx(q: str, top_k=TOP_K):
    """Hybrid retriever: semantic similarity + keyword boost (prefers templates/chatbot)."""
    if RAG_VECTORS.shape[0]==0 or not RAG_META:
        return []
    client=OpenAI()
    # semantic
    qemb=client.embeddings.create(model=EMB_MODEL, input=[q]).data[0].embedding
    qv = np.array(qemb,dtype=np.float32)
    qv /= (np.linalg.norm(qv)+1e-9)
    base = RAG_VECTORS / (np.linalg.norm(RAG_VECTORS,axis=1,keepdims=True)+1e-9)
    sims = base @ qv

    # keyword
    q_toks = set(_tokenize(q))  # <-- FIXED (no space)
    kw_scores = np.zeros(len(RAG_META), dtype=np.float32)
    for i, md in enumerate(RAG_META):
        t = md["text"].lower()
        hit = sum(1 for tok in q_toks if tok and tok in t)
        bonus = 0.03 * hit
        if "templates/chatbot" in (md["path"].replace("\\","/")).lower():
            bonus += 0.05 * hit
        kw_scores[i] = bonus

    combined = sims + kw_scores
    idx = combined.argsort()[::-1][:max(top_k, 2*top_k)]
    total = 0; out=[]
    for i in idx:
        md = RAG_META[i]
        out.append(md)
        total += len(md["text"])
        if total >= MAX_CONTEXT_CHARS:
            break
    return out[:top_k]

def _smart_answer(question: str) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        return ("AI answers are disabled because OPENAI_API_KEY is not set. "
                "Add it in your Render environment to enable the smart chatbot.")
    _ensure_index()
    ctx=_retrieve_ctx(question)
    blocks=[]; seen=set(); srcs=[]
    for r in ctx:
        p=r["path"]
        srcs.append(p)
        if p not in seen:
            seen.add(p)
            blocks.append(f"Source: {p}\n{r['text']}")
        else:
            blocks.append(r["text"])
    context="\n\n---\n\n".join(blocks) if blocks else "No project context found."
    client=OpenAI()
    system=("You are DSV’s project assistant. Answer clearly using the project context. "
            "If the context doesn’t contain the answer, say so briefly.")
    user=f"Question:\n{question}\n\nProject context:\n{context}"
    resp=client.chat.completions.create(
        model=AI_MODEL,
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=0.2,
    )
    answer = resp.choices[0].message.content.strip()
    if AI_DEBUG and srcs:
        uniq = list(dict.fromkeys(srcs))[:5]
        answer += "\n\n[Sources]\n" + "\n".join(uniq)
    return answer

@app.route("/smart_chat", methods=["POST"])
def smart_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"reply": "Hi! Ask me anything about this project—files, templates, rates, or how the app works."})
    try:
        return jsonify({"reply": _smart_answer(message)})
    except Exception as e:
        log.exception("smart_chat error")
        return jsonify({"reply": f"Sorry, an error occurred ({type(e).__name__})."}), 200

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    raw = (data.get("message") or "").strip()
    if not raw:
        return jsonify({"reply": "Hi! How can I help with DSV quotations today?"})
    try:
        return jsonify({"reply": _smart_answer(raw)})
    except Exception as e:
        log.exception("chat error")
        return jsonify({"reply": f"Sorry, an error occurred ({type(e).__name__})."}), 200

@app.route("/reindex", methods=["POST"])
def reindex():
    _ensure_index()
    return jsonify({
        "ok": True,
        "chunks": int(RAG_VECTORS.shape[0]),
        "paths": len({m['path'] for m in RAG_META})
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
