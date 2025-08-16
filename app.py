# app.py  — full version (files-first, fast RAG, PDF support, ChatGPT fallback, original generate logic kept)

from flask import Flask, render_template, request, send_file, jsonify, make_response
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_LINE_SPACING
import os, re, logging, json, hashlib, fnmatch, uuid
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

# Scan your project files (templates + chatbot folder + static + CHATBOT INFO)
RAG_FOLDERS = ["templates", "templates/chatbot", "static", "CHATBOT INFO"]
RAG_GLOBS   = ["*.py","*.js","*.ts","*.html","*.css","*.txt","*.md","*.docx","*.xlsx","*.pdf"]
EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", "generated"}

# Retrieval tuning (improved for better accuracy)
CHARS_PER_CHUNK   = 1200  # Larger chunks for more context
CHUNK_OVERLAP     = 200   # More overlap to avoid missing info
TOP_K             = 8     # More results to increase chance of finding answer
MAX_CONTEXT_CHARS = 9000  # More context for better answers
MAX_FILE_BYTES    = 1_500_000
MAX_TOTAL_CHUNKS  = 4000
EMBED_BATCH       = 32

# Behavior toggles
AI_DEBUG     = os.getenv("AI_DEBUG", "0") == "1"      # add [Sources]
TAG_SOURCES  = os.getenv("TAG_SOURCES", "0") == "1"   # prefix replies with source tag
STRICT_LOCAL = os.getenv("STRICT_LOCAL", "0") == "1"  # 0 = allow ChatGPT fallback

# Pre-init index globals
RAG_VECTORS = np.zeros((0, 1536), dtype=np.float32)
RAG_META    = []

# ---------------- Conversation memory (per user via cookie) ----------------
CONV = {}      # { cid: [ {"role":"user"|"assistant", "content":"..."} , ... ] }
MAX_TURNS = 10

def _get_cid():
    cid = request.cookies.get("cid")
    if not cid:
        cid = uuid.uuid4().hex
    return cid

def _get_history(cid):
    return CONV.get(cid, [])

def _append_history(cid, role, content):
    hist = CONV.get(cid, [])
    hist.append({"role": role, "content": (content or "").strip()})
    if len(hist) > MAX_TURNS:
        hist = hist[-MAX_TURNS:]
    CONV[cid] = hist

def _history_text_for_query(hist, max_chars=600):
    """Compact anchor from recent turns so short follow-ups inherit the topic."""
    if not hist:
        return ""
    last_user = [m.get("content","") for m in hist if m.get("role") == "user"][-3:]
    last_asst = [m.get("content","") for m in hist if m.get("role") == "assistant"][-1:]
    blob = " | ".join(last_user + last_asst)
    return blob[-max_chars:]

def _should_use_anchor(question: str) -> bool:
    """Use recent context only for short follow-ups (avoid sticky topics)."""
    s = (question or "").strip().lower()
    if len(s.split()) <= 5: return True
    if re.search(r"\b(how many|how much|what rate|which one|and|it|them|that|those|list|types|services)\b", s):
        return True
    return False

# ---------------- Flask ----------------
app = Flask(__name__)

@app.route("/")
def index():
    return render_template("form.html")

# ================== Matrix / pricing helpers (original logic) ==================
Tariff_PATH = "CL TARIFF - 2025 v3 (004) - UPDATED 6TH AUGUST 2025.xlsx"

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
    xls = pd.ExcelFile(Tariff_PATH)
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

# ---- pricing for one item (kept as original) ----
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

    # RMS (Premium / Normal)
    elif st.startswith("RMS — Premium"):
        unit, rate_unit, rate = "BOX", "BOX / MONTH", 5.0
        storage_fee = volume * days * (rate / 30.0)
    elif st.startswith("RMS — Normal"):
        unit, rate_unit, rate = "BOX", "BOX / MONTH", 3.0
        storage_fee = volume * days * (rate / 30.0)

    # WMS
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

# ---------- commodity helper ----------
def _clean_commodity(val: str) -> str:
    s = (val or "").strip()
    if not s: return ""
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return ""
    return s

# ---------- DOCX helpers (kept as original) ----------
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

# ---------- ROUTE: generate (original multi-item + VAS/Terms logic) ----------
@app.route("/generate", methods=["POST"])
def generate():
    storage_types = request.form.getlist("storage_type") or [request.form.get("storage_type", "")]
    volumes       = request.form.getlist("volume")       or [request.form.get("volume", 0)]
    days_list     = request.form.getlist("days")         or [request.form.get("days", 0)]
    wms_list      = request.form.getlist("wms")          or [request.form.get("wms", "No")]
    commodities_raw = request.form.getlist("commodity")  or [request.form.get("commodity", "")]
    canonical_commodity = next((c for c in (_clean_commodity(v) for v in commodities_raw) if c), "")

    # RMS sub-fields
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
            st_label = ("RMS — Premium AC Facility"
                        if "premium" in tier.lower() else "RMS — Normal AC Facility")
            vol = float(rms_boxes_list[i] or 0)  # boxes
            inc = True  # WMS always ON for RMS
            items.append(compute_item(st_label, vol, d, inc, com))
            continue

        if "open yard" in st_lower:
            inc = False

        if not raw_st:
            continue
        items.append(compute_item(raw_st, vol, d, inc, com))

    # WMS aggregation for RMS combos (kept from original)
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

    # Choose template
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

    # Fill placeholders
    if len(items) == 1:
        one = items[0]
        unit_for_display = one["unit"]; unit_rate_text = f"{one['rate']:.2f} AED / {one['rate_unit']}"
        wms_status = "" if one["category_openyard"] else ("INCLUDED" if one["include_wms"] else "NOT INCLUDED")
        storage_type_text = one["storage_type"]; days_text = str(one["days"]); volume_text = str(one["volume"])
        commodity_text = one.get("commodity") or canonical_commodity or "N/A"
    else:
        st_lines = []
        for it in items:
            qty = f"{it['volume']} {it['unit']}".strip()
            dur = f"{it['days']} day{'s' if it['days'] != 1 else ''}"
            st_lines.append(f"{it['storage_type']} ({qty} × {dur})")
        storage_type_text = "\n".join(st_lines)
        cm_lines = []
        for it in items:
            cm = it.get("commodity") or canonical_commodity or "N/A"
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

    # Insert VAS + Terms as per items (original logic)
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
        doc.add_paragraph("Terms & Conditions — Combined"); h = doc.paragraphs[-1]; h.runs[0].bold = True

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

# ========================== RAG (index + retrieval) ==========================
def _list_files_for_rag():
    files = []
    for folder in RAG_FOLDERS:
        base = Path(folder)
        if not base.exists():
            log.warning(f"RAG folder not found: {folder}")
            continue
        
        log.info(f"Scanning folder: {folder}")
        folder_files = 0
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            if not any(fnmatch.fnmatch(p.name, g) for g in RAG_GLOBS):
                continue
            try:
                size = p.stat().st_size
                if size > MAX_FILE_BYTES:
                    log.warning(f"File too large, skipping: {p} ({size} bytes)")
                    continue
                if size == 0:
                    log.warning(f"Empty file, skipping: {p}")
                    continue
            except Exception:
                pass
            files.append(p)
            folder_files += 1
        
        log.info(f"Found {folder_files} files in {folder}")
    
    # Prioritize CHATBOT INFO files
    chatbot_files = [f for f in files if "CHATBOT INFO" in str(f)]
    other_files = [f for f in files if "CHATBOT INFO" not in str(f)]
    files = chatbot_files + other_files
    
    seen, out = set(), []
    for p in files:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key); out.append(p)
    
    log.info(f"Total files for RAG: {len(out)} ({len(chatbot_files)} from CHATBOT INFO)")
    return out

def _read_pdf_file(p: Path) -> str:
    try:
        from pypdf import PdfReader
        log.info(f"Attempting to read PDF: {p}")
        reader = PdfReader(str(p))
        out = []
        total_pages = len(reader.pages)
        log.info(f"PDF {p.name} has {total_pages} pages")
        
        for i, page in enumerate(reader.pages[:120]):  # Limit to first 120 pages
            try:
                txt = page.extract_text()
                if txt and txt.strip():
                    clean_txt = txt.strip()
                    out.append(clean_txt)
                    if i < 3:  # Log first few pages for debugging
                        log.info(f"Page {i} text preview: {clean_txt[:200]}...")
            except Exception as e:
                log.warning(f"Error extracting text from page {i} of {p}: {e}")
                continue
        
        result = "\n".join(out)
        if result:
            log.info(f"Successfully extracted {len(result)} characters from PDF: {p.name}")
        else:
            log.warning(f"No text extracted from PDF: {p.name}")
        return result
    except ImportError as e:
        log.error(f"pypdf not available: {e}")
        return ""
    except Exception as e:
        log.error(f"Error reading PDF file {p}: {e}")
        return ""

def _file_text_for_rag(p: Path) -> str:
    suf = p.suffix.lower()
    if suf in (".txt",".md",".py",".js",".ts",".html",".css"):
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
    """Build embeddings once, cached by content signature; reload if files change."""
    if not os.getenv("OPENAI_API_KEY"):
        return np.zeros((0,1536),dtype=np.float32), []

    client=OpenAI()
    files=_list_files_for_rag()

    sig=[]
    for p in files:
        try: sig.append(f"{p}:{int(p.stat().st_mtime)}:{p.stat().st_size}")
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
    global RAG_VECTORS, RAG_META
    if RAG_VECTORS.shape[0]==0 or not RAG_META:
        RAG_VECTORS, RAG_META = _build_or_load_index_rag()
        log.info("RAG index ready: %d chunks from %d paths",
                 RAG_VECTORS.shape[0], len({m['path'] for m in RAG_META}))

# Build index once at startup
_ensure_index()

def _tokenize(s: str):
    return re.findall(r"[a-z0-9]+", (s or "").lower())

def _search_rag(query: str, top_k: int = TOP_K) -> list:
    """Enhanced semantic + keyword search with chatbot folder priority."""
    if RAG_VECTORS.shape[0] == 0: return []
    client = OpenAI()
    resp = client.embeddings.create(input=[query], model=EMB_MODEL)
    q_emb = np.array(resp.data[0].embedding, dtype=np.float32)
    scores = np.dot(RAG_VECTORS, q_emb)

    # Enhanced keyword matching with more comprehensive terms
    words = set(re.findall(r'\w+', query.lower()))
    storage_keywords = {'storage', 'warehouse', 'facility', 'depot'}
    rate_keywords = {'rate', 'price', 'cost', 'fee', 'charge', 'tariff', 'aed'}
    service_keywords = {'ac', 'non-ac', 'chemical', 'rms', 'standard', 'vas', 'open', 'yard'}
    company_keywords = {'dsv', 'solutions', 'pjsc'}

    for i, meta in enumerate(RAG_META):
        txt = meta['text'].lower()
        path = meta['path'].lower()

        # Exact phrase matching for better accuracy
        if any(phrase in txt for phrase in [w for w in words if len(w) > 2]):
            scores[i] += 0.2

        # Category-specific keyword boosts
        storage_hits = sum(1 for w in storage_keywords if w in txt and w in query.lower())
        rate_hits = sum(1 for w in rate_keywords if w in txt and w in query.lower())
        service_hits = sum(1 for w in service_keywords if w in txt and w in query.lower())
        company_hits = sum(1 for w in company_keywords if w in txt and w in query.lower())

        scores[i] += 0.3 * storage_hits
        scores[i] += 0.35 * rate_hits  # Boost rate-related content more
        scores[i] += 0.25 * service_hits
        scores[i] += 0.2 * company_hits

        # MASSIVE boost for chatbot info folder (your main documentation)
        if 'chatbot info' in path or 'chatbot' in path:
            scores[i] += 0.8

        # High boost for specific rate/terms PDFs
        if path.endswith('.pdf'):
            scores[i] += 0.3
            # Extra boost for specific document types
            if any(doc_type in path for doc_type in ['rates', 'standard rates', 'chemical rates', 'rms', 'terms']):
                scores[i] += 0.4

        # Boost template files for VAS and terms info
        if 'templates/' in path and any(keyword in path for keyword in ['vas', 'standard', 'chemical', 'rms']):
            scores[i] += 0.3

        # Penalize very short chunks that might not have context
        if len(meta['text']) < 100:
            scores[i] -= 0.1

    top_ids = np.argsort(scores)[-top_k:][::-1]
    # Lower threshold to catch more relevant content
    return [RAG_META[i] for i in top_ids if scores[i] > 0.05]

def _build_context_from_docs(search_results):
    """Build context from search results with smart prioritization."""
    if not search_results:
        return None

    # Sort results by source priority and relevance score
    def source_priority(result):
        path = result['path'].lower()
        if 'chatbot info' in path or 'chatbot' in path:
            return 0  # Highest priority
        elif 'rates' in path or 'terms' in path:
            return 1  # High priority
        elif 'templates' in path:
            return 2  # Medium priority
        else:
            return 3  # Lower priority

    # Sort by priority first, then by text length (longer chunks often have more complete info)
    sorted_results = sorted(search_results, key=lambda x: (source_priority(x), -len(x['text'])))

    context_parts = []
    total_chars = 0
    max_context = 12000  # Increased context window for better accuracy
    sources_used = set()

    # First pass: Add high-priority sources
    for result in sorted_results:
        if source_priority(result) > 1:  # Skip lower priority in first pass
            continue

        chunk = result["text"]
        path = result["path"]

        # For high-priority sources, include more complete chunks
        if total_chars + len(chunk) > max_context * 0.7:
            continue

        source_info = f"Source: {path}"
        context_parts.append(f"{source_info}\n{chunk}")
        total_chars += len(chunk) + len(source_info) + 2
        sources_used.add(path)

    # Second pass: Fill remaining space with other sources
    for result in sorted_results:
        if result['path'] in sources_used:
            continue

        chunk = result["text"]
        path = result["path"]

        if total_chars + len(chunk) > max_context:
            # Try to fit a partial chunk if there's space
            remaining_space = max_context - total_chars - 100
            if remaining_space > 200:
                chunk = chunk[:remaining_space]
            else:
                break

        source_info = f"Source: {path}"
        context_parts.append(f"{source_info}\n{chunk}")
        total_chars += len(chunk) + len(source_info) + 2
        sources_used.add(path)

    if not context_parts:
        return None

    return "\n\n---\n\n".join(context_parts)

def _has_strong_ctx(ctx_blocks: list, question: str) -> bool:
    if not ctx_blocks:
        return False
    total_len = sum(len(md["text"]) for md in ctx_blocks)
    if total_len < 400: # Minimum content length for "strong" context
        return False
    q_toks = set(t for t in _tokenize(question) if len(t) > 2) # Use tokens from question
    sample = " ".join(md["text"] for md in ctx_blocks[:3]).lower() # Check first few chunks
    hit = sum(1 for t in q_toks if t in sample)
    return hit >= 2 # Require at least 2 keyword hits

def _needs_bullets(ans: str) -> bool:
    """True if answer ends with 'summary:' / 'including:' / 'types:' etc. and has no bullets."""
    if not ans or not ans.strip():
        return True
    s = ans.strip()
    # Only trigger if it's JUST a heading with no substantial content
    if re.fullmatch(r"(?is)\s*[\w\s\-\(\)/&]+:\s*", s):
        return True
    # For brief responses, don't force bullets unless it's clearly incomplete
    if len(s) > 50:
        return False
    # Only force bullets for very short responses that end with list indicators
    cue = re.search(r"(?is)\b(summary|overview|including|includes|include|the following|as follows|list|types|services|features)\b\s*:?\s*$", s)
    has_bullets = re.search(r"\n\s*(?:[-•*]|\d+\.)\s+", s) is not None
    return bool(cue) and not has_bullets and len(s) < 30

def _bulletize_with_llm(question: str, ctx_blocks: list, draft_heading: str = "") -> str:
    client = OpenAI()
    context = _build_context_from_docs(ctx_blocks) # Use the context builder
    if not context: context = "No relevant project files found."

    heading = draft_heading.strip() if draft_heading else ""
    system = ("Rewrite the answer as concise bullet points using ONLY the provided context. "
              "Return 5–12 bullets. No preamble, no closing line.")
    user = f"Question: {question}\n\nContext:\n{context}\n\n{('Heading: ' + heading) if heading else ''}\n\nWrite only bullet points:"
    resp=client.chat.completions.create(model=AI_MODEL,
                                        messages=[{"role":"system","content":system},{"role":"user","content":user}],
                                        temperature=0.1, max_tokens=400)
    return resp.choices[0].message.content.strip()

def _maybe_force_bullets(question: str, ctx_blocks: list, ans: str) -> str:
    # Only force bullets for very incomplete responses
    if not _needs_bullets(ans) or len(ans.strip()) > 100:
        return ans
    # bulletize using same context; if empty, still try general knowledge
    bulletized = _bulletize_with_llm(question, ctx_blocks, draft_heading=ans)
    if bulletized and not _needs_bullets(bulletized):
        return bulletized
    return _bulletize_with_llm(question, [], draft_heading=ans) or ans # Fallback to general knowledge bulleting

# ---------------- Bullet safety (avoid empty "summary:" endings) ----------------
# (This function is kept but _needs_bullets and _bulletize_with_llm are used instead)
def _is_nonanswer(text: str) -> bool:
    if not text: return True
    bad = [r"does not provide", r"not present in the context", r"based on the provided context.*cannot",
           r"no project context found", r"i (?:do|don’t|don't) (?:have|see)", r"not specified in the context"]
    t = text.strip().lower()
    return any(re.search(p, t) for p in bad)

def _llm_with_context(question: str, context: str, history_msgs: list) -> str:
    """Use project context to answer with improved accuracy."""
    client=OpenAI()
    if not context: context = "No project context found."

    # Create enhanced prompt with context
    system_msg = """You are a DSV logistics assistant. Answer questions using ONLY the provided context from DSV's official documents.

CRITICAL INSTRUCTIONS:
- Carefully read through ALL the provided context before answering
- Use ONLY information explicitly stated in the provided context
- If multiple sources contain relevant info, synthesize the complete answer
- Quote specific rates, terms, and details exactly as written
- If the answer is in the context but incomplete, provide what's available and note what's missing

SEARCH STRATEGY:
- Look for exact keywords and phrases from the question in the context
- Check multiple document sources if available
- Pay attention to different document types (rates, terms, VAS, etc.)

RESPONSE STYLE:
- Be direct and factual but complete
- Always include exact rates with units (AED/CBM/DAY, AED/SQM/MONTH, etc.)
- Include specific details like minimums, duration-based pricing, conditions
- Mention the source document when helpful for credibility

COMPANY INFO FROM CONTEXT:
- Extract company details, services, fleet info directly from the provided documents
- Include specific operational details found in the context
- Reference facility locations, service types as mentioned in documents

If no relevant information is found in the context, clearly state "I don't see that specific information in the provided documents."

    """
    msgs=[{"role":"system","content":system_msg}]
    for m in history_msgs[-MAX_TURNS:]:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role":"system","content": f"DSV OFFICIAL DOCUMENTS CONTEXT:\n{context}"})
    msgs.append({"role":"user","content": question})

    resp=client.chat.completions.create(model=AI_MODEL, messages=msgs, temperature=0.1, max_tokens=500)
    ans = resp.choices[0].message.content.strip()

    # Check if the answer is just a stub and try to bulletize if necessary
    if _is_nonanswer(ans):
        return ans

    # Attempt to bulletize if it looks like a list heading without bullets
    ans = _maybe_force_bullets(question, [], ans)

    if TAG_SOURCES: ans = "[From files] " + ans
    if AI_DEBUG and not context.startswith("No project context found."):
        # Extract sources from context for AI_DEBUG
        sources = re.findall(r"Source: ([^\n]+)", context)
        if sources:
            uniq_sources = list(dict.fromkeys(sources))[:5]
            ans += "\n\n[Sources]\n" + "\n".join(uniq_sources)
    return ans

def _llm_general_answer(question: str, history_msgs: list) -> str:
    """Plain ChatGPT fallback (no files)."""
    client=OpenAI()
    system_msg = """You are a DSV logistics assistant. Give brief, direct answers.

RESPONSE STYLE:
- Keep answers concise (1-3 sentences unless asked for details)
- Lead with the most important information
- No lengthy explanations unless specifically requested

DSV SERVICES:
- Global transport and logistics company
- Road transport, air & sea freight, contract logistics
- Large fleet: trucks, trailers, specialized vehicles
- UAE operations: Dubai, Abu Dhabi facilities

RATES (when applicable):
- AC storage: 1.2-3.5 AED/CBM/DAY
- Non-AC storage: 0.8-2.5 AED/CBM/DAY
- Include minimums and duration-based pricing

Keep responses brief unless user asks for more details."""

    msgs=[{"role":"system","content":system_msg}]
    for m in history_msgs[-MAX_TURNS:]:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role":"user","content":question})
    resp=client.chat.completions.create(model=AI_MODEL, messages=msgs, temperature=0.2, max_tokens=300)
    ans = resp.choices[0].message.content.strip()
    ans = _maybe_force_bullets(question, [], ans)
    if TAG_SOURCES: ans = "[General knowledge] " + ans
    return ans

def _smart_answer(question: str, history_msgs: list) -> str:
    """Search your files first with enhanced matching, fallback to ChatGPT."""
    docs = _search_rag(question)

    # If we found good matches, use them
    if docs:
        context = _build_context_from_docs(docs)
        # Log more details for debugging
        sources = [doc['path'] for doc in docs[:5]]  # Top 5 sources
        log.info(f"Found {len(docs)} docs, context: {len(context)} chars")
        log.info(f"Top sources: {sources}")
        log.info(f"Question: {question[:100]}...")
        return _llm_with_context(question, context, history_msgs)

    # If no matches but query seems DSV-related, try broader search
    dsv_related = any(keyword in question.lower() for keyword in
                     ['dsv', 'storage', 'rate', 'warehouse', 'ac', 'chemical', 'rms', 'standard', 'terms'])

    if dsv_related:
        # Try a broader search with lower threshold
        broader_docs = []
        if RAG_VECTORS.shape[0] > 0:
            client = OpenAI()
            resp = client.embeddings.create(input=[question], model=EMB_MODEL)
            q_emb = np.array(resp.data[0].embedding, dtype=np.float32)
            scores = np.dot(RAG_VECTORS, q_emb)

            # Lower threshold, prioritize chatbot folder
            for i, meta in enumerate(RAG_META):
                if 'chatbot' in meta['path'].lower():
                    scores[i] += 0.4

            top_ids = np.argsort(scores)[-8:][::-1]  # Get more results
            broader_docs = [RAG_META[i] for i in top_ids if scores[i] > 0.05]

        if broader_docs:
            context = _build_context_from_docs(broader_docs)
            log.info(f"Broader search found {len(broader_docs)} docs for DSV query")
            return _llm_with_context(question, context, history_msgs)

    log.info("No RAG matches, using general LLM")
    return _llm_general_answer(question, history_msgs)

# ---------------- Chat routes ----------------
@app.route("/smart_chat", methods=["POST"])
def smart_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    cid = _get_cid()
    if not message:
        resp = make_response(jsonify({"reply": "Hi! Ask me anything about this project—files, templates, rates, or how it works."}))
        resp.set_cookie("cid", cid, httponly=True, samesite="Lax")
        return resp
    try:
        _append_history(cid, "user", message)
        reply = _smart_answer(message, _get_history(cid))
        _append_history(cid, "assistant", reply)
        resp = make_response(jsonify({"reply": reply}))
        resp.set_cookie("cid", cid, httponly=True, samesite="Lax")
        return resp
    except Exception as e:
        log.exception("smart_chat error")
        resp = make_response(jsonify({"reply": f"Sorry, an error occurred ({type(e).__name__})."}), 200)
        resp.set_cookie("cid", cid, httponly=True, samesite="Lax")
        return resp

# Backward-compatible /chat (for legacy JS)
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    raw = (data.get("message") or "").strip()
    cid = _get_cid()
    if not raw:
        resp = make_response(jsonify({"reply": "Hi! How can I help with DSV quotations today?"}))
        resp.set_cookie("cid", cid, httponly=True, samesite="Lax")
        return resp
    try:
        _append_history(cid, "user", raw)
        reply = _smart_answer(raw, _get_history(cid))
        _append_history(cid, "assistant", reply)
        resp = make_response(jsonify({"reply": reply}))
        resp.set_cookie("cid", cid, httponly=True, samesite="Lax")
        return resp
    except Exception as e:
        log.exception("chat error")
        resp = make_response(jsonify({"reply": f"Sorry, an error occurred ({type(e).__name__})."}), 200)
        resp.set_cookie("cid", cid, httponly=True, samesite="Lax")
        return resp

# Manual reindex when you add/replace files
@app.route("/reindex", methods=["POST"])
def reindex():
    try: os.remove(RAG_INDEX_PATH)
    except FileNotFoundError: pass
    try: os.remove(RAG_META_PATH)
    except FileNotFoundError: pass
    global RAG_VECTORS, RAG_META
    RAG_VECTORS = np.zeros((0,1536),dtype=np.float32); RAG_META = []
    _ensure_index()
    return jsonify({"ok": True, "chunks": int(RAG_VECTORS.shape[0]), "paths": len({m['path'] for m in RAG_META})})

# ===== Build index at startup =====
_ensure_index()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
