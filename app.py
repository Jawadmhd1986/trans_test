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

# index scope (explicit chatbot folder included)
RAG_FOLDERS = ["templates", "templates/chatbot", "static"]
RAG_GLOBS = ["*.py","*.js","*.ts","*.html","*.css","*.txt","*.md","*.docx","*.xlsx","*.pdf"]
EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", "generated"}

# retrieval tuning
CHARS_PER_CHUNK   = 900
CHUNK_OVERLAP     = 150
TOP_K             = 6
MAX_CONTEXT_CHARS = 7000
MAX_FILE_BYTES    = 1_500_000
MAX_TOTAL_CHUNKS  = 4000
EMBED_BATCH       = 32

# behavior toggles
AI_DEBUG          = os.getenv("AI_DEBUG", "0") == "1"     # add [Sources]
TAG_SOURCES       = os.getenv("TAG_SOURCES", "0") == "1"  # label replies
STRICT_LOCAL      = os.getenv("STRICT_LOCAL", "0") == "1" # 0 = allow ChatGPT fallback

# pre-init
RAG_VECTORS = np.zeros((0, 1536), dtype=np.float32)
RAG_META = []

# ---- conversational memory (per user via cookie) ----
CONV = {}              # { cid: [ {"role":"user"|"assistant", "content": "..."} , ... ] }
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
    if not hist:
        return ""
    last_user = [m.get("content", "") for m in hist if m.get("role") == "user"][-3:]
    asst_msgs = [m.get("content", "") for m in hist if m.get("role") == "assistant"]
    last_asst = asst_msgs[-1:] if asst_msgs else []
    blob = " | ".join(last_user + last_asst)
    return blob[-max_chars:]


def _should_use_anchor(question: str) -> bool:
    s = (question or "").strip().lower()
    if len(s.split()) <= 5: return True
    if re.search(r"\b(how many|how much|what rate|which one|and|it|them|that)\b", s): return True
    return False

# ---------------- Flask ----------------
app = Flask(__name__)

# ================== Matrix / pricing helpers ==================
TARIFF_PATH = "CL TARIFF - 2025 v3 (004) - UPDATED 6TH AUGUST 2025.xlsx"

def _is_num(x):
    return isinstance(x, (int, float)) and pd.notna(x)

def _daily_rate_from_row(df, r, from_col=2, to_col=3):
    candidates, fallback = [], []
    for c in range(df.shape[1]):
        if c in (from_col, to_col): continue
        v = df.iat[r, c]
        if _is_num(v):
            v = float(v)
            if 1.2 <= v <= 10.0:
                candidates.append(v)
            elif v > 0:
                fallback.append(v)
    if candidates: return min(candidates)
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
        "category_rms": is_rms, "commodity": (commodity or "").strip(),
    }

def _clean_commodity(val: str) -> str:
    s = (val or "").strip()
    if not s: return ""
    if re.fullmatch(r"\d+(\.\d+)?", s): return ""
    return s

# ---- DOCX helpers ----
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
        if t in ("Standard VAS","Chemical VAS","Open Yard VAS",
                 "Terms & Conditions — Chemical","Terms & Conditions — Open Yard",
                 "Value Added Service Rates (Standard VAS)","Value Added Service Rates",
                 "RMS VAS","Terms & Conditions — RMS"):
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
        hdr[0].text, hdr[1].text, hdr[2].text = "Item","Unit Rate","Amount (AED)"
    _clear_quote_table_keep_header(qt)

    for it in items:
        r = qt.add_row().cells
        r[0].text = it['storage_type']
        r[1].text = f"{it['rate']:.2f} AED / {it['rate_unit']}"
        r[2].text = f"{it['storage_fee']:,.2f} AED"
        if it["include_wms"]:
            r = qt.add_row().cells
            r[0].text = f"WMS - {it['storage_type']} ({it['months']} month{'s' if it['months']!=1 else ''})"
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
    targets = ("Storage Period:","Storage Size:","We trust that the rates","Yours Faithfully","DSV Solutions PJSC","Validity:")
    to_remove = []
    for p in doc.paragraphs:
        t = (p.text or "").strip().lower()
        if any(t.startswith(x.lower()) for x in targets) or "value added service rates" in t:
            to_remove.append(p)
    for p in set(to_remove):
        el = p._element; pa = el.getparent()
        if pa is not None: pa.remove(el)

def _append_terms_from_template(path, terms_heading="Storage Terms and Conditions:", end_markers=("Validity:","We trust that")):
    src = _safe_docx(path)
    if not src: return []
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
    tags = ["[VAS_STANDARD]","[/VAS_STANDARD]","[VAS_CHEMICAL]","[/VAS_CHEMICAL]","[VAS_OPENYARD]","[/VAS_OPENYARD]","[VAS_RMS]","[/VAS_RMS]"]
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

# ---------------- ROUTE: generate (quotation) ----------------
@app.route("/generate", methods=["POST"])
def generate():
    storage_types = request.form.getlist("storage_type") or [request.form.get("storage_type", "")]
    volumes       = request.form.getlist("volume")       or [request.form.get("volume", 0)]
    days_list     = request.form.getlist("days")         or [request.form.get("days", 0)]
    wms_list      = request.form.getlist("wms")          or [request.form.get("wms", "No")]
    commodities_raw = request.form.getlist("commodity")  or [request.form.get("commodity", "")]
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
            st_label = ("RMS - Premium FM200 Archiving AC Facility" if "premium" in tier.lower() else "RMS - Normal AC Facility")
            vol = float(rms_boxes_list[i] or 0)  # boxes
            inc = True  # WMS always ON for RMS
            items.append(compute_item(st_label, vol, d, inc, com))
            continue

        if "open yard" in st_lower:
            inc = False

        if not raw_st:
            continue
        items.append(compute_item(raw_st, vol, d, inc, com))

    # (WMS combo aggregation logic omitted here for brevity—keep your working version)

    if not items:
        items = [compute_item("AC", 0.0, 0, False)]

    today_str = datetime.today().strftime("%d %b %Y")

    if len(items) == 1:
        st0 = items[0]["storage_type"].lower()
        if "chemical" in st0: template_path = "templates/Chemical VAS.docx"
        elif "open yard" in st0: template_path = "templates/Open Yard VAS.docx"
        elif st0.startswith("rms"):
            template_path = "templates/RMS VAS.docx" if _safe_docx("templates/RMS VAS.docx") else "templates/Standard VAS.docx"
        else:
            template_path = "templates/Standard VAS.docx"
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
            st_lines.append(f"{it['storage_type']} ({qty} x {dur})")
        storage_type_text = "\n".join(st_lines)
        cm_lines = []
        for it in items:
            cm = it.get("commodity") or _clean_commodity(request.form.get("commodity")) or "N/A"
            cm_lines.append(f"{it['storage_type']} - {cm}")
        commodity_text = "\n".join(cm_lines)
        unit_for_display = ""; unit_rate_text = "—"
        days_text = "VARIOUS"; volume_text = "VARIOUS"; wms_status = "SEE BREAKDOWN"

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
    qt = _rebuild_quotation_table(doc, items, grand_total)

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
        if not base.exists(): continue
        for p in base.rglob("*"):
            if not p.is_file(): continue
            if any(part in EXCLUDE_DIRS for part in p.parts): continue
            if not any(fnmatch.fnmatch(p.name, g) for g in RAG_GLOBS): continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES: continue
            except Exception: pass
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
            txt = (page.extract_text() or "").strip()
            if txt: out.append(txt)
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
            d=Document(str(p)); parts=[]
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
            xls=pd.ExcelFile(str(p)); out=[]
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
    if not os.getenv("OPENAI_API_KEY"): return np.zeros((0,1536),dtype=np.float32), []
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
    global RAG_VECTORS, RAG_META
    if RAG_VECTORS.shape[0] == 0 or not RAG_META:
        RAG_VECTORS, RAG_META = _build_or_load_index_rag()
        log.info("RAG index ready: %d chunks from %d paths", RAG_VECTORS.shape[0], len({m['path'] for m in RAG_META}))

# Build index once at startup
_ensure_index()

# ================== Deterministic intents (non-sticky) ==================
def answer_fleet_intent(question, _history_msgs):
    s = (question or "").lower()
    if not re.search(r"\b(fleet|truck(?:s)? types?|vehicle(?:s)? types?|transport fleet|our fleet)\b", s): return ""
    return (
        "DSV fleet summary:\n"
        "- Flatbeds - general cargo and containers (~22-25 tons)\n"
        "- Lowbeds - heavy/oversized machinery (up to ~60 tons)\n"
        "- Tippers - bulk materials (~15-20 tons)\n"
        "- Box trucks - weather-protected goods (~5-10 tons)\n"
        "- Reefer trucks - +2 to -22 C cold chain (~3-12 tons)\n"
        "- Double trailers - high-volume long-haul (~50-60 tons total)\n"
        "- Small city trucks - last-mile (~1-3 tons)"
    )

def answer_21k_intent(question, _history_msgs):
    s = (question or "").lower()
    if "21k" not in s: return ""
    if not (("warehouse" in s) or ("mussafah" in s) or ("dsv" in s)): return ""
    return (
        "DSV 21K warehouse (Mussafah) - key facts:\n"
        "- Size and height: 21,000 sqm, ~15 m clear height\n"
        "- Racking: Selective, VNA, Drive-in; racks ~12 m with 6 pallet levels\n"
        "- Aisle widths: Selective 2.95-3.3 m; VNA 1.95 m; Drive-in 2.0 m\n"
        "- Per-bay capacity: 14 Standard pallets or 21 Euro pallets\n"
        "- Chambers: 7 (clients incl. ADNOC, ZARA, PSN, Civil Defense)\n"
        "- RMS area: FM-200 protected archiving zone inside 21K\n"
        "- Certifications: GDSP/ISO-aligned security and safety systems"
    )

def answer_container_intent(question, _history_msgs):
    s = (question or "").lower()
    is_20 = bool(re.search(r"\b20\s*ft\b|\b20ft\b|\b20\s*feet\b", s))
    is_40 = bool(re.search(r"\b40\s*ft\b|\b40ft\b|\b40\s*feet\b", s))
    is_hc = bool(re.search(r"\bhc\b|high\s*cube", s))
    is_reefer = "reefer" in s or "refrigerated" in s
    is_open_top = "open top" in s
    is_flat_rack = "flat rack" in s
    if not any([is_20,is_40,is_hc,is_reefer,is_open_top,is_flat_rack]) and "container" not in s: return ""
    if is_reefer:
        return ("Reefer container:\n- Sizes: 20ft and 40ft\n- Temp: +2 to -25 C\n- Use: food/pharma/perishables\n- Example 40ft: 12.2m x 2.44m x 2.59m (~67 CBM)")
    if is_open_top: return ("Open Top container (20ft/40ft):\n- Tarpaulin roof, same base dims\n- Top loading via crane/forklift\n- Ideal for tall cargo")
    if is_flat_rack: return ("Flat Rack container:\n- No sides/roof\n- For oversized cargo (vehicles, generators, heavy equipment)")
    if is_40 and is_hc: return ("40ft High Cube:\n- 12.2m x 2.44m x 2.90m\n- ~76 CBM\n- For high-volume cargo")
    if is_40: return ("40ft container:\n- 12.2m x 2.44m x 2.59m\n- ~67 CBM\n- Max payload ~30,400 kg")
    if is_20: return ("20ft container:\n- 6.10m x 2.44m x 2.59m\n- ~33 CBM\n- Max payload ~28,000 kg")
    return ("Common containers:\n- 20ft ~33 CBM (~28,000 kg)\n- 40ft ~67 CBM (~30,400 kg)\n- 40ft HC ~76 CBM\n- Reefer (20/40ft)\n- Open Top\n- Flat Rack")

# ================== Retrieval & bullet safety ==================
def _tokenize(s: str):
    return re.findall(r"[a-z0-9]+", (s or "").lower())

def _has_strong_ctx(ctx, question: str) -> bool:
    if not ctx: return False
    total_len = sum(len(md["text"]) for md in ctx)
    if total_len < 400: return False
    q_toks = set(t for t in _tokenize(question) if len(t) > 2)
    sample = " ".join(md["text"] for md in ctx[:3]).lower()
    return sum(1 for t in q_toks if t in sample) >= 2

def _retrieve_ctx(query_str: str, top_k=TOP_K):
    if RAG_VECTORS.shape[0]==0 or not RAG_META: return []
    client=OpenAI()
    qemb=client.embeddings.create(model=EMB_MODEL, input=[query_str]).data[0].embedding
    qv = np.array(qemb,dtype=np.float32); qv /= (np.linalg.norm(qv)+1e-9)
    base = RAG_VECTORS / (np.linalg.norm(RAG_VECTORS,axis=1,keepdims=True)+1e-9)
    sims = base @ qv
    q_toks = set(_tokenize(query_str))
    kw_scores = np.zeros(len(RAG_META), dtype=np.float32)
    for i, md in enumerate(RAG_META):
        t = md["text"].lower()
        hit = sum(1 for tok in q_toks if tok and tok in t)
        bonus = 0.03 * hit
        if "templates/chatbot" in (md["path"].replace("\\","/")).lower(): bonus += 0.05 * hit
        kw_scores[i] = bonus
    combined = sims + kw_scores
    idx = combined.argsort()[::-1][:max(top_k, 2*top_k)]
    total = 0; out=[]
    for i in idx:
        md = RAG_META[i]; out.append(md); total += len(md["text"])
        if total >= MAX_CONTEXT_CHARS: break
    return out[:top_k]

def _ctx_text_from_blocks(ctx_blocks, limit=4000):
    parts=[]; total=0
    for md in ctx_blocks:
        t=(md.get("text") or "").strip()
        if not t: continue
        if total+len(t)>limit: t=t[:max(0,limit-total)]
        parts.append(t); total += len(t)
        if total>=limit: break
    return "\n\n---\n\n".join(parts)

def _needs_bullets(ans: str) -> bool:
    if not ans or not ans.strip(): return True
    s = ans.strip()
    header_only = re.fullmatch(r"(?is)\s*([A-Za-z0-9 \-\(\)\/&]+:)\s*", s) is not None
    says_summary = re.search(r"(?i)\b(summary|overview|specifications|specs|includes|the following)\b\s*:?\s*$", s) is not None
    has_bullets = re.search(r"\n\s*(?:[-•*]|\d+\.)\s", s) is not None
    return (header_only or says_summary) and not has_bullets

def _bulletize_with_llm(question: str, ctx_blocks: list, draft_heading: str = "") -> str:
    client = OpenAI()
    context = _ctx_text_from_blocks(ctx_blocks, limit=3500)
    heading = draft_heading.strip() if draft_heading else ""
    system = ("Rewrite the answer as concise bullet points using ONLY the provided context. "
              "Return 5-12 bullets. No preamble, no closing line.")
    user = f"Question: {question}\n\nContext:\n{context}\n\n{('Heading: ' + heading) if heading else ''}\n\nWrite only bullet points:"
    resp = client.chat.completions.create(model=AI_MODEL, messages=[{"role":"system","content":system},{"role":"user","content":user}], temperature=0.1, max_tokens=220)
    return resp.choices[0].message.content.strip()

def _maybe_force_bullets(question: str, ctx_blocks: list, ans: str) -> str:
    if not _needs_bullets(ans): return ans
    if re.search(r"(?i)fleet|trailer|truck", question):
        forced = answer_fleet_intent(question, []);  return forced or ans
    if re.search(r"(?i)21k|mussafah", question):
        forced = answer_21k_intent(question, []);    return forced or ans
    bulletized = _bulletize_with_llm(question, ctx_blocks, draft_heading=ans)
    return bulletized or ans

# ================== LLM answering ==================
def _answer_with_ctx(question: str, ctx_blocks: list, history_msgs: list) -> str:
    client=OpenAI()
    blocks=[]; seen=set(); srcs=[]
    for r in ctx_blocks:
        p=r["path"]; srcs.append(p)
        if p not in seen: seen.add(p); blocks.append(f"Source: {p}\n{r['text']}")
        else: blocks.append(r["text"])
    context="\n\n---\n\n".join(blocks) if blocks else "No project context found."

    system=("You are DSV's project assistant. Use conversation history to resolve follow-ups. "
            "Answer from the project context. If you say 'summary', follow it with bullet points. "
            "If the context does not contain the answer, say so briefly.")
    msgs=[{"role":"system","content":system}]
    for m in history_msgs[-MAX_TURNS:]: msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role":"system","content": f"Project context:\n{context}"})
    msgs.append({"role":"user","content": question})
    resp=client.chat.completions.create(model=AI_MODEL, messages=msgs, temperature=0.2, max_tokens=300)
    ans = resp.choices[0].message.content.strip()
    ans = _maybe_force_bullets(question, ctx_blocks, ans)
    if TAG_SOURCES: ans = "[From files] " + ans
    if AI_DEBUG and srcs:
        uniq = list(dict.fromkeys(srcs))[:5]; ans += "\n\n[Sources]\n" + "\n".join(uniq)
    return ans

def _llm_general_answer(question: str, history_msgs: list) -> str:
    client=OpenAI()
    system=("You are a helpful logistics assistant. Use the prior conversation to keep topic continuity. "
            "If you say 'summary', follow it with bullet points. Answer accurately and concisely.")
    msgs=[{"role":"system","content":system}]
    for m in history_msgs[-MAX_TURNS:]: msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role":"user","content":question})
    resp=client.chat.completions.create(model=AI_MODEL, messages=msgs, temperature=0.2, max_tokens=250)
    ans = resp.choices[0].message.content.strip()
    ans = _maybe_force_bullets(question, [], ans)
    if TAG_SOURCES: ans = "[General knowledge] " + ans
    return ans

def _is_nonanswer(text: str) -> bool:
    if not text: return True
    bad = [r"does not provide", r"not present in the context", r"based on the provided context.*cannot", r"no project context found",
           r"i (?:do|don’t|don't) (?:have|see)", r"not specified in the context"]
    t = text.strip().lower()
    return any(re.search(p, t) for p in bad)

def _smart_answer(question: str, history_msgs: list) -> str:
    if not os.getenv("OPENAI_API_KEY"): return ("AI answers are disabled because OPENAI_API_KEY is not set.")
    # Deterministic intents first (non-sticky)
    pre = (answer_container_intent(question, history_msgs) or
           answer_fleet_intent(question, history_msgs) or
           answer_21k_intent(question, history_msgs) or
           answer_storage_rate_intent(question, history_msgs))
    if pre: return pre

    # Build retrieval query (anchor only for short follow-ups)
    anchor = _history_text_for_query(history_msgs) if _should_use_anchor(question) else ""
    query_for_retrieval = question if not anchor else f"{question}\n\nRecent topic: {anchor}"
    ctx=_retrieve_ctx(query_for_retrieval)

    if not _has_strong_ctx(ctx, question):
        if STRICT_LOCAL: return "I do not have this in your saved files. (General knowledge disabled.)"
        return _llm_general_answer(question, history_msgs)

    ans = _answer_with_ctx(question, ctx, history_msgs)
    if _is_nonanswer(ans):
        if STRICT_LOCAL: return "I do not have this in your saved files. (General knowledge disabled.)"
        return _llm_general_answer(question, history_msgs)
    return ans

# ================== Storage-rate intent (matrix-driven) ==================
def _fmt_num(x):
    try:
        if x == float("inf"): return "inf"
        return f"{float(x):g}"
    except Exception: return str(x)

def _format_bands(label, bands):
    if not bands: return f"{label}: (no data)"
    rows = [f"- {_fmt_num(f)}-{_fmt_num(t)} CBM: {r:.2f} AED / CBM / DAY" for f,t,r in bands]
    return f"{label}:\n" + "\n".join(rows)

def _storage_rate_text_for_standard(kind):
    family = "ac" if kind == "AC" else "dry"
    try:
        lt = MATRIX[family]["lt1m"]; ge = MATRIX[family]["ge1m"]
    except Exception:
        return "I could not load the standard storage bands from the matrix."
    parts = [
        f"Standard {kind} storage rates (AED / CBM / DAY):",
        _format_bands("Period < 30 days", lt),
        _format_bands("Period >= 30 days", ge),
        "Note: band applies by CBM and duration."
    ]
    return "\n".join(parts)

def _storage_rate_text_for_specific(stype):
    s = (stype or "").lower()
    if s == "chemicals ac": return "Chemicals AC storage: 3.50 AED / CBM / DAY."
    if s in ("chemicals non-ac (non-dg)", "chemicals non-ac"): return "Chemicals Non-AC (Non-DG) storage: 2.50 AED / CBM / DAY."
    if s == "chemicals non-ac (dg)": return "Chemicals Non-AC (DG) storage: 3.00 AED / CBM / DAY."
    if "open yard – kizad" in s: return "Open Yard - KIZAD: 125 AED / SQM / YEAR (pro-rata)."
    if stype == "Open Yard – Mussafah (Open Yard)": return "Open Yard - Mussafah (Open Yard): 15 AED / SQM / MONTH (pro-rata)."
    if stype == "Open Yard – Mussafah (Open Yard Shed)": return "Open Yard - Mussafah (Open Yard Shed): 35 AED / SQM / MONTH (pro-rata)."
    if stype == "Open Yard – Mussafah (Jumbo Bag)": return "Open Yard - Mussafah (Jumbo Bag): 19 AED / BAG / MONTH (pro-rata)."
    if s.startswith("rms — premium"): return "RMS - Premium AC archiving: 5.00 AED / BOX / MONTH."
    if s.startswith("rms — normal"): return "RMS - Normal AC archiving: 3.00 AED / BOX / MONTH."
    return ""

def _guess_storage_kind_from_text(text, history_anchor=""):
    s = f"{text} {history_anchor}".lower()
    if "chemical" in s or "haz" in s:
        if re.search(r"\b(ac|a\.c\.|air ?cond)\b", s): return "Chemicals AC"
        if "dg" in s: return "Chemicals Non-AC (DG)"
        return "Chemicals Non-AC (Non-DG)"
    if "open yard" in s or "kizad" in s or "mussafah yard" in s:
        if "kizad" in s: return "Open Yard – KIZAD"
        if "shed" in s:  return "Open Yard – Mussafah (Open Yard Shed)"
        if "jumbo" in s: return "Open Yard – Mussafah (Jumbo Bag)"
        return "Open Yard – Mussafah (Open Yard)"
    if "rms" in s or "record management" in s or "archiv" in s:
        if "premium" in s: return "RMS — Premium FM200 Archiving AC Facility"
        return "RMS — Normal AC Facility"
    if re.search(r"\b(ac|a\.c\.|air ?cond)\b", s): return "AC"
    if re.search(r"\bnon[- ]?ac\b", s) or "dry" in s: return "Non-AC"
    if "open shed" in s: return "Open Shed"
    return None

def answer_storage_rate_intent(question, history_msgs):
    anchor = _history_text_for_query(history_msgs) if _should_use_anchor(question) else ""
    q = (question or "").lower()
    if not re.search(r"\b(rate|price|tariff)\b", q) and "storage" not in q: return ""
    kind = _guess_storage_kind_from_text(question, anchor)
    if not kind: return ""
    if kind in ("AC","Non-AC","Open Shed"): return _storage_rate_text_for_standard(kind)
    spec = _storage_rate_text_for_specific(kind)
    return spec or ""

# ---------------- Routes with cookie + memory ----------------
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

# manual reindex (when you add files)
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

# ====== Build index at startup ======
def _ensure_index():
    global RAG_VECTORS, RAG_META
    if RAG_VECTORS.shape[0] == 0 or not RAG_META:
        RAG_VECTORS, RAG_META = _build_or_load_index_rag()
        log.info("RAG index ready: %d chunks from %d paths", RAG_VECTORS.shape[0], len({m['path'] for m in RAG_META}))

_ensure_index()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
