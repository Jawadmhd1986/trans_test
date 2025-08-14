from flask import Flask, render_template, request, send_file, jsonify
from docx import Document
import os
import re
from datetime import datetime

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("form.html")

@app.route("/generate", methods=["POST"])
def generate():
    storage_type = request.form.get("storage_type", "")
    volume = float(request.form.get("volume", 0))
    days = int(request.form.get("days", 0))
    include_wms = request.form.get("wms", "No") == "Yes"
    commodity = request.form.get("commodity", "").strip()
    today_str = datetime.today().strftime("%d %b %Y")

    # Pick template based on storage_type
    if "chemical" in storage_type.lower():
        template_path = "templates/Chemical VAS.docx"
    elif "open yard" in storage_type.lower():
        template_path = "templates/Open Yard VAS.docx"
    else:
        template_path = "templates/Standard VAS.docx"

    doc = Document(template_path)

    # Rates / units
    if storage_type == "AC":
        rate, unit, rate_unit = 2.5, "CBM", "CBM / DAY"
        storage_fee = volume * days * rate
    elif storage_type == "Non-AC":
        rate, unit, rate_unit = 2.0, "CBM", "CBM / DAY"
        storage_fee = volume * days * rate
    elif storage_type == "Open Shed":
        rate, unit, rate_unit = 1.8, "CBM", "CBM / DAY"
        storage_fee = volume * days * rate
    elif storage_type == "Chemicals AC":
        rate, unit, rate_unit = 3.5, "CBM", "CBM / DAY"
        storage_fee = volume * days * rate
    elif storage_type == "Chemicals Non-AC":
        rate, unit, rate_unit = 2.7, "CBM", "CBM / DAY"
        storage_fee = volume * days * rate
    elif "kizad" in storage_type.lower():
        rate, unit, rate_unit = 125, "SQM", "SQM / YEAR"
        storage_fee = volume * days * (rate / 365)
    elif "mussafah" in storage_type.lower():
        rate, unit, rate_unit = 160, "SQM", "SQM / YEAR"
        storage_fee = volume * days * (rate / 365)
    else:
        rate, unit, rate_unit, storage_fee = 0, "CBM", "CBM / DAY", 0

    storage_fee = round(storage_fee, 2)
    months = max(1, days // 30)
    is_open_yard = "open yard" in storage_type.lower()
    wms_fee = 0 if is_open_yard or not include_wms else 1500 * months
    total_fee = round(storage_fee + wms_fee, 2)

    placeholders = {
        "{{STORAGE_TYPE}}": storage_type,
        "{{DAYS}}": str(days),
        "{{VOLUME}}": str(volume),
        "{{UNIT}}": unit,
        "{{WMS_STATUS}}": "" if is_open_yard else ("INCLUDED" if include_wms else "NOT INCLUDED"),
        "{{UNIT_RATE}}": f"{rate:.2f} AED / {rate_unit}",
        "{{STORAGE_FEE}}": f"{storage_fee:,.2f} AED",
        "{{WMS_FEE}}": f"{wms_fee:,.2f} AED",
        "{{TOTAL_FEE}}": f"{total_fee:,.2f} AED",
        "{{TODAY_DATE}}": today_str,
        "{{COMMODITY}}": commodity or "N/A",
    }

    # Replace placeholders even if Word split them across runs
    def replace_in_paragraph(paragraph, mapping):
        if not paragraph.runs:
            return
        full_text = "".join(run.text for run in paragraph.runs)
        new_text = full_text
        for key, val in mapping.items():
            new_text = new_text.replace(key, val)
        if new_text != full_text:
            for run in paragraph.runs:
                run.text = ""
            paragraph.runs[0].text = new_text

    def replace_placeholders(doc_obj, mapping):
        for p in doc_obj.paragraphs:
            replace_in_paragraph(p, mapping)
        for table in doc_obj.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        replace_in_paragraph(p, mapping)

    replace_placeholders(doc, placeholders)

    # Delete blocks bracketed by [TAG] ... [/TAG]
    def _delete_block_in_paragraphs(doc_obj, start_tag, end_tag):
        inside = False
        to_delete = []
        for i, p in enumerate(doc_obj.paragraphs):
            if start_tag in p.text:
                inside = True
                to_delete.append(i)
            elif end_tag in p.text:
                to_delete.append(i)
                inside = False
            elif inside:
                to_delete.append(i)
        for i in reversed(to_delete):
            elem = doc_obj.paragraphs[i]._element
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)

    def _delete_block_in_tables(doc_obj, start_tag, end_tag):
        for table in doc_obj.tables:
            for row in table.rows:
                for cell in row.cells:
                    inside = False
                    to_remove = []
                    for p in cell.paragraphs:
                        if start_tag in p.text:
                            inside = True
                            to_remove.append(p)
                        elif end_tag in p.text:
                            to_remove.append(p)
                            inside = False
                        elif inside:
                            to_remove.append(p)
                    for p in to_remove:
                        elem = p._element
                        parent = elem.getparent()
                        if parent is not None:
                            parent.remove(elem)

    def delete_block(doc_obj, start_tag, end_tag):
        _delete_block_in_paragraphs(doc_obj, start_tag, end_tag)
        _delete_block_in_tables(doc_obj, start_tag, end_tag)

    # Keep only the relevant VAS section
    if "open yard" in storage_type.lower():
        delete_block(doc, "[VAS_STANDARD]", "[/VAS_STANDARD]")
        delete_block(doc, "[VAS_CHEMICAL]", "[/VAS_CHEMICAL]")
    elif "chemical" in storage_type.lower():
        delete_block(doc, "[VAS_STANDARD]", "[/VAS_STANDARD]")
        delete_block(doc, "[VAS_OPENYARD]", "[/VAS_OPENYARD]")
    else:
        delete_block(doc, "[VAS_CHEMICAL]", "[/VAS_CHEMICAL]")
        delete_block(doc, "[VAS_OPENYARD]", "[/VAS_OPENYARD]")

    os.makedirs("generated", exist_ok=True)
    filename_prefix = commodity if commodity else "quotation"
    filename = f"Quotation_{filename_prefix}.docx"
    output_path = os.path.join("generated", filename)
    doc.save(output_path)

    return send_file(output_path, as_attachment=True)

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    raw = data.get("message", "") if data else ""
    raw = raw if isinstance(raw, str) else str(raw)

    # Quick reply if first non-empty line is a short greeting
    first_line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    if re.match(r"^(hi|hello|hey|good (morning|evening))\b", first_line, re.I) and len(first_line.split()) <= 3:
        return jsonify({"reply": "Hello! I'm here to help with anything related to DSV logistics, transport, or warehousing."})

    # Collapse to one line for matching
    text = " ".join(ln.strip() for ln in raw.splitlines() if ln.strip())

    # Normalization (consolidated + fixed indentation/variable usage)
    def normalize(s: str) -> str:
        s = s.lower().strip()

        # Common chat language
        s = re.sub(r"\bu\b", "you", s)
        s = re.sub(r"\bur\b", "your", s)
        s = re.sub(r"\br\b", "are", s)
        s = re.sub(r"how\s*r\s*u", "how are you", s)
        s = re.sub(r"\bpls\b", "please", s)
        s = re.sub(r"\bplz\b", "please", s)
        s = re.sub(r"\bthx\b", "thanks", s)
        s = re.sub(r"\binfo\b", "information", s)
        s = re.sub(r"\bassist\b", "help", s)
        s = re.sub(r"\bhru\b", "how are you", s)
        s = re.sub(r"\bh\s*r\s*u\b", "how are you", s)
        s = re.sub(r"how\s*u\s*doing", "how are you", s)
        s = re.sub(r"\bhw\b", "how", s)
        s = re.sub(r"\bwht\b", "what", s)
        s = re.sub(r"\bcn\b", "can", s)
        s = re.sub(r"\bwhats up\b", "how are you", s)
        s = re.sub(r"\bwho r u\b", "who are you", s)

        # Logistics & warehouse short forms
        s = re.sub(r"\bwh\b", "warehouse", s)
        s = re.sub(r"\bw/w\b", "warehouse", s)
        s = re.sub(r"\bw\/h\b", "warehouse", s)
        s = re.sub(r"\binv\b", "inventory", s)
        s = re.sub(r"\btemp zone\b", "temperature zone", s)
        s = re.sub(r"\btemp\b", "temperature", s)
        s = re.sub(r"\bwms system\b", "wms", s)
        s = re.sub(r"\bwms\b", "warehouse management system", s)

        # Transportation & locations
        s = re.sub(r"\brak\b", "ras al khaimah", s)
        s = re.sub(r"\babudhabi\b", "abu dhabi", s)
        s = re.sub(r"\bdxb\b", "dubai", s)
        s = re.sub(r"\bdubaii\b", "dubai", s)
        s = re.sub(r"\bdubal\b", "dubai", s)
        s = re.sub(r"\bdubia\b", "dubai", s)
        s = re.sub(r"\babu dabi\b", "abu dhabi", s)
        s = re.sub(r"\bt&c\b", "terms and conditions", s)
        s = re.sub(r"\bt and c\b", "terms and conditions", s)

        # Industry abbreviations
        s = re.sub(r"\bo&g\b", "oil and gas", s)
        s = re.sub(r"\bdg\b", "dangerous goods", s)
        s = re.sub(r"\bfmcg\b", "fast moving consumer goods", s)

        # Quotation & VAS
        s = re.sub(r"\bdoc\b", "documentation", s)
        s = re.sub(r"\bdocs\b", "documentation", s)
        s = re.sub(r"\bmsds\b", "material safety data sheet", s)
        s = re.sub(r"\bvas\b", "value added services", s)
        s = re.sub(r"\bquote\b", "quotation", s)
        s = re.sub(r"\bquation\b", "quotation", s)
        s = re.sub(r"\bquotatoin\b", "quotation", s)
        s = re.sub(r"\boffer\b", "quotation", s)
        s = re.sub(r"\bproposal\b", "quotation", s)
        s = re.sub(r"\bproposl\b", "quotation", s)
        s = re.sub(r"\bvases\b", "value added services", s)
        s = re.sub(r"\bvalus added services\b", "value added services", s)

        # E-commerce variations
        s = re.sub(r"\be[\s\-]?commerce\b", "ecommerce", s)
        s = re.sub(r"\bshop logistics\b", "ecommerce", s)

        # Logistics models
        s = re.sub(r"\b3\.5pl\b", "three and half pl", s)
        s = re.sub(r"\b2pl\b", "second party logistics", s)
        s = re.sub(r"\b3pl\b", "third party logistics", s)
        s = re.sub(r"\b4pl\b", "fourth party logistics", s)
        s = re.sub(r"\b5pl\b", "fifth party logistics", s)
        s = re.sub(r"\b6pl\b", "sixth party logistics", s)

        # Fleet & vehicle types
        s = re.sub(r"\breefer truck\b|\bchiller truck\b|\bcold truck\b", "refrigerated truck", s)
        s = re.sub(r"\bchiller\b", "refrigerated truck", s)
        s = re.sub(r"\bcity truck\b", "small truck", s)
        s = re.sub(r"\bev truck\b", "electric truck", s)
        s = re.sub(r"\bcity delivery\b", "last mile", s)
        s = re.sub(r"\btransprt\b", "transport", s)
        s = re.sub(r"\btrnasport\b", "transport", s)
        s = re.sub(r"\bmachineries\b", "machinery", s)
        s = re.sub(r"\bmhe\b", "material handling equipment", s)
        s = re.sub(r"\breefer\s+tr+ucks?\b", "reefer truck", s)
        s = re.sub(r"\breefer truck\b", "reefer truck", s)

        # Container unit typos & variants
        s = re.sub(r"\b20feet\b", "20 ft", s)
        s = re.sub(r"\b20foot\b", "20 ft", s)
        s = re.sub(r"\b20feet container\b", "20 ft container", s)
        s = re.sub(r"\b20ft\b", "20 ft", s)
        s = re.sub(r"\brefeer\b", "reefer", s)
        s = re.sub(r"\bchilled container\b", "reefer container", s)
        s = re.sub(r"\b40feet\b", "40 ft", s)
        s = re.sub(r"\b40foot\b", "40 ft", s)
        s = re.sub(r"\b40feet container\b", "40 ft container", s)
        s = re.sub(r"\b40ft\b", "40 ft", s)

        # Fire system
        s = re.sub(r"\bfm200\b", "fm 200", s)

        # Misc business terms
        s = re.sub(r"\bkitting\b", "kitting and assembly", s)
        s = re.sub(r"\btagging\b", "labeling", s)
        s = re.sub(r"\basset tagging\b", "asset labeling", s)
        s = re.sub(r"\btransit store\b", "transit warehouse", s)
        s = re.sub(r"\basset mgmt\b", "asset management", s)
        s = re.sub(r"\bmidday break\b", "summer break", s)
        s = re.sub(r"\bwharehouse\b", "warehouse", s)
        s = re.sub(r"\bwmsytem\b", "wms", s)
        s = re.sub(r"\bopen yrd\b", "open yard", s)
        s = re.sub(r"\bstorge\b", "storage", s)
        s = re.sub(r"\bstorag\b", "storage", s)
        s = re.sub(r"\bchecmical\b", "chemical", s)
        s = re.sub(r"\bstandrad\b", "standard", s)
        s = re.sub(r"\blabelling\b", "labeling", s)

        # Strip non-alphanumeric except spaces and periods
        s = re.sub(r"[^a-z0-9\s\.]", "", s)
        return s

    message = normalize(text)

    def match(patterns):
        return any(re.search(p, message) for p in patterns)

    # --- Containers (All Types + Flexible Unit Recognition) ---
    if match([
        r"\b20\s*(ft|feet|foot)\b", r"\btwenty\s*(ft|feet|foot)?\b",
        r"\b20 ft\b.*", r".*20.*container.*", r"container.*20 ft", r"^20 ft$", r"^20 feet$", r"20ft spec"
    ]):
        return jsonify({"reply":
            "📦 **20ft Container Specs**:\n"
            "- Length: 6.1m\n"
            "- Width: 2.44m\n"
            "- Height: 2.59m\n"
            "- Capacity: ~33 CBM\n"
            "- Max Payload: ~28,000 kg\n\n"
            "Ideal for compact or heavy cargo like pallets, boxes, or general freight."
        })

    if match([
        r"\b40\s*(ft|feet|foot)\b", r"\bforty\s*(ft|feet|foot)?\b",
        r"\b40 ft\b.*", r".*40.*container.*", r"container.*40 ft", r"^40 ft$", r"^40 feet$", r"40ft spec"
    ]):
        return jsonify({"reply":
            "📦 **40ft Container Specs**:\n"
            "- Length: 12.2m\n"
            "- Width: 2.44m\n"
            "- Height: 2.59m\n"
            "- Capacity: ~67 CBM\n"
            "- Max Payload: ~30,400 kg\n\n"
            "Perfect for palletized or high-volume cargo. Widely used for full truckload and global sea freight."
        })

    if match([
        r"\bhighcube\b", r"high cube", r"40\s*(ft|feet|foot)\s*high cube",
        r"high cube container", r"40ft.*high cube", r"high cube spec", r"taller container"
    ]):
        return jsonify({"reply":
            "⬆️ **40ft High Cube Container Specs**:\n\n"
            "- Same length/width as standard 40ft:\n"
            "  • Length: 12.2m\n"
            "  • Width: 2.44m\n"
            "- **Height: 2.9m** (vs 2.59m standard)\n"
            "- Capacity: ~76 CBM\n\n"
            "**Ideal for voluminous cargo** where height matters — such as light bulk goods, furniture, or machines requiring upright transport."
        })

    if match([
        r"\breefer\b", r"reefer container", r"refrigerated container", r"chiller container",
        r"cold storage container", r"reefer.*(20|40)ft", r"reefer specs", r"reefer box"
    ]):
        return jsonify({"reply":
            "❄️ **Reefer (Refrigerated) Containers**:\n\n"
            "- Available in **20ft** and **40ft** sizes\n"
            "- Insulated with temperature control: **+2°C to –25°C**\n"
            "- Used for: food, pharmaceuticals, perishables\n"
            "- Plug-in units with cooling system (electric or diesel)\n\n"
            "**Specs Example (40ft Reefer):**\n"
            "- Length: 12.2m, Width: 2.44m, Height: 2.59m\n"
            "- Capacity: ~67 CBM\n\n"
            "Let me know if you want 20ft specs or details for sea/road use!"
        })

    if match([
        r"open top container", r"open top", r"top open", r"open roof", r"no roof container",
        r"container.*open top", r"container.*no roof", r"crane loaded container", r"top loading container"
    ]):
        return jsonify({"reply":
            "🏗 **Open Top Container Specs**:\n\n"
            "- Length: 20ft or 40ft\n"
            "- No solid roof — uses removable tarpaulin cover\n"
            "- Same base dimensions as standard container\n"
            "- Allows top loading via crane or forklift\n\n"
            "**Used for:**\n"
            "- Tall cargo (e.g., pipes, steel coils, machinery)\n"
            "- Oversized height loads\n"
            "- Construction or industrial freight requiring vertical access"
        })

    if match([r"flat rack", r"no sides container", r"flat rack container"]):
        return jsonify({"reply": "Flat Rack containers have no sides or roof, perfect for oversized cargo such as vehicles, generators, or heavy equipment."})

    if match([r"\bsme\b", r"sme container", r"what is sme", r"sme size", r"sme container size"]):
        return jsonify({"reply": "In logistics, **SME** usually refers to Small and Medium Enterprises, but in UAE context, 'SME container' can also mean modular containers customized for SME use — often used for short-term cargo storage or small-scale import/export."})

    if match([
        r"\bcontainers?\b", r"container types", r"types of containers",
        r"container sizes", r"container overview", r"container specs", r"container info"
    ]):
        return jsonify({"reply": "📦 Here are the main container types and their specs: 20ft, 40ft, High Cube, Reefer, Flat Rack, Open Top, SME... Let me know which you'd like in detail."})

    # --- Pallet Types, Sizes, and Positions ---
    if match([
        r"\bpallets\b", r"pallet types", r"types of pallets", r"pallet size", r"pallet sizes", r"pallet dimensions",
        r"standard pallet", r"euro pallet", r"pallet specs", r"tell me about pallets",
        r"what.*pallet.*used", r"pallet info", r"pallet.*per bay"
    ]):
        return jsonify({"reply":
            "DSV uses two main pallet types in its 21K warehouse:\n\n"
            "🟦 **Standard Pallet**:\n- Size: 1.2m × 1.0m\n- Load capacity: ~1,000 kg\n- Fits **14 pallets per bay**\n\n"
            "🟨 **Euro Pallet**:\n- Size: 1.2m × 0.8m\n- Load capacity: ~800 kg\n- Fits **21 pallets per bay**\n\n"
            "Pallets are used for racking, picking, and transport. DSV also offers VAS like pallet loading, shrink wrapping, labeling, and stretch film wrapping for safe handling."
        })
# --- All Storage Rates at Once (catch "all rates") ---
    if match([
    r"^all\s+rates?$",
    r"^all\s+storage\s+rates?$",
    r"^full\s+rates?$",
    r"^complete\s+rates?$",
    r"^show\s*all\s*rates$",
    r"all.*storage.*rates?",
    r"complete.*storage.*rates?",
    r"all.*rates?",
    r"list.*storage.*fees",
    r"storage.*rate.*overview",
    r"summary.*storage.*rates?",
    r"show.*all.*storage.*charges",
    r"storage.*rates?.*all",
    r"rates?\s*for\s*all\s*storage"
]):
        return jsonify({"reply":
        "**Here are the current DSV Abu Dhabi storage rates:**\n\n"
        "**📦 Standard Storage:**\n"
        "- AC: 2.5 AED/CBM/day\n"
        "- Non-AC: 2.0 AED/CBM/day\n"
        "- Open Shed: 1.8 AED/CBM/day\n\n"
        "**🧪 Chemical Storage:**\n"
        "- Chemical AC: 3.5 AED/CBM/day\n"
        "- Chemical Non-AC: 2.7 AED/CBM/day\n\n"
        "**🏗 Open Yard Storage:**\n"
        "- KIZAD: 125 AED/SQM/year\n"
        "- Mussafah: 160 AED/SQM/year\n\n"
        "*WMS fee applies to indoor storage unless excluded. For a full quotation, fill out the form.*"
    })

    # --- Storage Rate Initial Question ---
    if match([
        r"storage rate[s]?$", r"\brates\b", r"storage", r"storage cost",
        r"how much.*storage", r"quotation.*storage only", r"rates", r"rate",
        r"pricing of storage", r"cost of storage", r"rate for storage", r"all storage rates"
    ]) and not re.search(r"(vas|value added|reefer|refrigerated truck|truck|trailer|lowbed|flatbed|tipper|box truck)", message):
        return jsonify({"reply": "Which type of storage are you asking about? Standard, Chemicals, or Open Yard?"})

    # --- Standard Storage Follow-ups ---
    if match([r"^standard$", r"standard storage"]):
        return jsonify({"reply": "Do you mean Standard AC, Standard Non-AC, or Open Shed?"})

    if match([r"standard ac", r"ac standard", r"standard ac storage"]):
        return jsonify({"reply": "Standard AC storage is 2.5 AED/CBM/day. Standard VAS applies."})

    if match([r"standard non ac", r"non ac standard", r"standard non ac storage"]):
        return jsonify({"reply": "Standard Non-AC storage is 2.0 AED/CBM/day. Standard VAS applies."})

    if match([r"^ac$", r"\bstandard ac\b", r"ac storage", r"ac only"]):
        return jsonify({"reply": "Standard AC storage is 2.5 AED/CBM/day. Standard VAS applies."})

    if match([r"^non ac$", r"\bstandard non ac\b", r"non-ac storage", r"non ac only"]):
        return jsonify({"reply": "Standard Non-AC storage is 2.0 AED/CBM/day. Standard VAS applies."})

    if match([r"^shed$", r"open shed", r"shed storage", r"open shed only", r"standard open shed", r"open shed storage rate"]):
        return jsonify({"reply": "Open Shed storage is 1.8 AED/CBM/day. Standard VAS applies."})

    # --- Chemical Storage Follow-ups ---
    if match([r"^chemical$", r"^chemicals$", r"chemicals storage only", r"chemical storage only"]):
        return jsonify({"reply": "Do you mean Chemical AC or Chemical Non-AC?"})

    if match([r"chemical ac", r"ac chemical", r"chemical ac storage", r"chemical ac storage rate", r"^chemical ac$"]):
        return jsonify({"reply": "Chemical AC storage is 3.5 AED/CBM/day. Chemical VAS applies."})

    if match([r"chemical non ac", r"non ac chemical", r"chemical non ac storage", r"chemical non ac rate", r"^chemical non ac$"]):
        return jsonify({"reply": "Chemical Non-AC storage is 2.7 AED/CBM/day. Chemical VAS applies."})

    # --- Open Yard Overview ---
    if match([
        r"tell me about open yard", r"open yard info", r"open yard overview", r"what is open yard",
        r"open yard introduction", r"open yard facility", r"describe open yard"
    ]):
        return jsonify({"reply":
            "🏗️ **DSV Open Yard Overview:**\n\n"
            "- 📍 **Mussafah Open Yard**: 160 AED/SQM/year\n"
            "- 📍 **KIZAD Open Yard**: 125 AED/SQM/year\n"
            "- 🔲 Total Area: **360,000 SQM** across both sites\n"
            "- ✅ Ideal for containers, equipment, heavy goods\n"
            "- 🔧 VAS includes forklifts, cranes, and lifting services\n\n"
            "📧 For availability, contact Antony Jeyaraj at **antony.jeyaraj@dsv.com**"
        })

    # --- Open Yard Storage ---
    if match([r"^open yard$", r"open yard storage", r"open yard rate", r"open yard storage rate"]):
        return jsonify({"reply": "Do you mean Open Yard in Mussafah or KIZAD?"})

    if match([r"open yard mussafah", r"mussafah open yard", r"rate.*mussafah open yard", r"^mussafah$"]):
        return jsonify({"reply": "Open Yard Mussafah storage is **160 AED/SQM/year**. WMS is excluded. For availability, contact Antony Jeyaraj at antony.jeyaraj@dsv.com."})

    if match([r"open yard kizad", r"kizad open yard", r"rate.*kizad open yard", r"^kizad$"]):
        return jsonify({"reply": "Open Yard KIZAD storage is **125 AED/SQM/year**. WMS is excluded. For availability, contact Antony Jeyaraj at antony.jeyaraj@dsv.com."})
 
    # General VAS prompt if user just says 'vas' / 'vas rates'
    if match([
        r"^vas$",
        r"^vas\s*rates?$",
        r"^value\s*added\s*services$",
        r"^value\s*added\s*service$",
        r"^vas\s*details$"
    ]):
        return jsonify({"reply":
            "Which VAS do you need?\n\n"
            "🟦 Type **Standard VAS** for AC/Non-AC/Open Shed\n"
            "🧪 Type **Chemical VAS** for hazmat/chemicals\n"
            "🏗 Type **Open Yard VAS** for forklifts/cranes"})
    
# --- VAS: Aggregate / Prompt ---
    if match([
    r"^all\s*vas(?:es)?\s*(rates|list)?$",
    r"^give\s*me\s*all\s*vas(?:es)?\s*(rates|list)?$",
    r"^show\s*all\s*vas(?:es)?",
    r"^complete\s*vas\s*(rates|list)?$",
    r"^full\s*vas\s*(rates|list)?$",
    r"all.*value\s*added\s*services",
    r"vas.*(full|all|complete).*"
]):
        return jsonify({"reply":
        "**📦 Standard VAS:**\n"
        "- In/Out Handling: 20 AED/CBM\n"
        "- Pallet Loading: 12 AED/pallet\n"
        "- Documentation: 125 AED/set\n"
        "- Packing with pallet: 85 AED/CBM\n"
        "- Inventory Count: 3,000 AED/event\n"
        "- Case Picking: 2.5 AED/carton\n"
        "- Sticker Labeling: 1.5 AED/label\n"
        "- Shrink Wrapping: 6 AED/pallet\n"
        "- VNA Usage: 2.5 AED/pallet\n\n"
        "**🧪 Chemical VAS:**\n"
        "- Handling (Palletized): 20 AED/CBM\n"
        "- Handling (Loose): 25 AED/CBM\n"
        "- Documentation: 150 AED/set\n"
        "- Packing with pallet: 85 AED/CBM\n"
        "- Inventory Count: 3,000 AED/event\n"
        "- Inner Bag Picking: 3.5 AED/bag\n"
        "- Sticker Labeling: 1.5 AED/label\n"
        "- Shrink Wrapping: 6 AED/pallet\n\n"
        "**🏗 Open Yard VAS:**\n"
        "- Forklift (3T–7T): 90 AED/hr\n"
        "- Forklift (10T): 200 AED/hr\n"
        "- Forklift (15T): 320 AED/hr\n"
        "- Mobile Crane (50T): 250 AED/hr\n"
        "- Mobile Crane (80T): 450 AED/hr\n"
        "- Container Lifting: 250 AED/lift\n"
        "- Container Stripping (20ft): 1,200 AED/hr"
    })
    
# --- All Storage Rates at Once ---
    if ("value added services" not in message) and match([
    r"\ball\s+storage\s+rates?\b",
    r"\b(all|complete|full)\b.*\bstorage\b.*\brates?\b",
    r"\bsummary\b.*\bstorage\b.*\brates?\b",
    r"\blist\b.*\bstorage\b.*\b(fees|rates?)\b",
    r"\bshow\b.*\ball\b.*\bstorage\b.*\b(charges|rates?)\b",
]):
        return jsonify({"reply":
            "**Here are the current DSV Abu Dhabi storage rates:**\n\n"
            "**📦 Standard Storage:**\n"
            "- AC: 2.5 AED/CBM/day\n"
            "- Non-AC: 2.0 AED/CBM/day\n"
            "- Open Shed: 1.8 AED/CBM/day\n\n"
            "**🧪 Chemical Storage:**\n"
            "- Chemical AC: 3.5 AED/CBM/day\n"
            "- Chemical Non-AC: 2.7 AED/CBM/day\n\n"
            "**🏗 Open Yard Storage:**\n"
            "- KIZAD: 125 AED/SQM/year\n"
            "- Mussafah: 160 AED/SQM/year\n\n"
            "*WMS fee applies to indoor storage unless excluded. For a full quotation, fill out the form.*"
        })

    # --- VAS rates ---
    if match([
        r"standard vas", r"standard", r"standard value added services", r"normal vas", r"normal value added services",
        r"handling charges", r"pallet charges", r"vas for ac", r"value added services for ac",
        r"vas for non ac", r"value added services for non ac",
        r"vas for open shed", r"value added services for open shed"
    ]):
        return jsonify({"reply":
            "Standard VAS includes:\n- In/Out Handling: 20 AED/CBM\n- Pallet Loading: 12 AED/pallet\n- Documentation: 125 AED/set\n- Packing with pallet: 85 AED/CBM\n- Inventory Count: 3,000 AED/event\n- Case Picking: 2.5 AED/carton\n- Sticker Labeling: 1.5 AED/label\n- Shrink Wrapping: 6 AED/pallet\n- VNA Usage: 2.5 AED/pallet"
        })

    if match([
        r"chemical vas", r"chemical value added services",
        r"vas for chemical", r"value added services for chemical",
        r"hazmat vas", r"hazmat value added services",
        r"dangerous goods vas", r"dangerous goods value added services"
    ]):
        return jsonify({"reply":
            "Chemical VAS includes:\n- Handling (Palletized): 20 AED/CBM\n- Handling (Loose): 25 AED/CBM\n- Documentation: 150 AED/set\n- Packing with pallet: 85 AED/CBM\n- Inventory Count: 3,000 AED/event\n- Inner Bag Picking: 3.5 AED/bag\n- Sticker Labeling: 1.5 AED/label\n- Shrink Wrapping: 6 AED/pallet"
        })

    if match([
        r"open yard vas", r"open yard", r"open yard value added services", r"yard equipment",
        r"forklift rate", r"crane rate", r"container lifting", r"yard charges"
    ]):
        return jsonify({"reply":
            "Open Yard VAS includes:\n- Forklift (3T–7T): 90 AED/hr\n- Forklift (10T): 200 AED/hr\n- Forklift (15T): 320 AED/hr\n- Mobile Crane (50T): 250 AED/hr\n- Mobile Crane (80T): 450 AED/hr\n- Container Lifting: 250 AED/lift\n- Container Stripping (20ft): 1,200 AED/hr"
        })

    if match([r"^standard$", r"standard vas", r"standard value added services", r"standard service"]):
        return jsonify({"reply":
            "🟦 **Standard VAS includes:**\n"
            "- In/Out Handling: 20 AED/CBM\n"
            "- Pallet Loading: 12 AED/pallet\n"
            "- Documentation: 125 AED/set\n"
            "- Packing with pallet: 85 AED/CBM\n"
            "- Inventory Count: 3,000 AED/event\n"
            "- Case Picking: 2.5 AED/carton\n"
            "- Sticker Labeling: 1.5 AED/label\n"
            "- Shrink Wrapping: 6 AED/pallet\n"
            "- VNA Usage: 2.5 AED/pallet"
        })

    if match([r"^chemical$", r"chemical vas", r"chemical value added services", r"chemical service"]):
        return jsonify({"reply":
            "🧪 **Chemical VAS includes:**\n"
            "- Handling (Palletized): 20 AED/CBM\n"
            "- Handling (Loose): 25 AED/CBM\n"
            "- Documentation: 150 AED/set\n"
            "- Packing with pallet: 85 AED/CBM\n"
            "- Inventory Count: 3,000 AED/event\n"
            "- Inner Bag Picking: 3.5 AED/bag\n"
            "- Sticker Labeling: 1.5 AED/label\n"
            "- Shrink Wrapping: 6 AED/pallet"
        })

    if match([r"^open yard$", r"open yard vas", r"open yard value added services", r"yard vas"]):
        return jsonify({"reply":
            "🏗 **Open Yard VAS includes:**\n"
            "- Forklift (3T–7T): 90 AED/hr\n"
            "- Forklift (10T): 200 AED/hr\n"
            "- Forklift (15T): 320 AED/hr\n"
            "- Mobile Crane (50T): 250 AED/hr\n"
            "- Mobile Crane (80T): 450 AED/hr\n"
            "- Container Lifting: 250 AED/lift\n"
            "- Container Stripping (20ft): 1,200 AED/hr"
        })

    # --- 21K Warehouse ---
    if match([r"rack height|rack levels|pallets per bay|racking"]):
        return jsonify({"reply": "21K warehouse racks are 12m tall with 6 pallet levels. Each bay holds 14 Standard pallets or 21 Euro pallets."})

    if match([r"\b21k\b", r"tell me about 21k", r"what is 21k", r"21k warehouse", r"21k dsv", r"main warehouse", r"mussafah.*21k"]):
        return jsonify({"reply":
            "21K is DSV’s main warehouse in Mussafah, Abu Dhabi. It is 21,000 sqm with a clear height of 15 meters. The facility features:\n"
            "- 3 rack types: Selective, VNA, and Drive-in\n"
            "- Rack height: 12m with 6 pallet levels\n"
            "- Aisle widths: Selective (2.95–3.3m), VNA (1.95m), Drive-in (2.0m)\n"
            "- 7 chambers used by clients like ADNOC, ZARA, PSN, and Civil Defense\n"
            "- Fully equipped with fire systems, access control, and RMS for document storage\n"
            "Chambers range from **1,000–5,000 sqm** and together can accommodate **~35,000 CBM**."
        })

    if match([r"\bgdsp\b", r"what is gdsp", r"gdsp certified", r"gdsp warehouse", r"gdsp compliance"]):
        return jsonify({"reply": "GDSP stands for Good Distribution and Storage Practices. It ensures that warehouse operations comply with global standards for the safe handling, storage, and distribution of goods, especially pharmaceuticals and sensitive materials. DSV’s warehouses in Abu Dhabi are GDSP certified."})

    if match([r"\biso\b", r"what iso", r"iso certified", r"tell me about iso", r"dsv iso", r"which iso standards"]):
        return jsonify({"reply":
            "DSV facilities in Abu Dhabi are certified with multiple ISO standards:\n- **ISO 9001**: Quality Management\n- **ISO 14001**: Environmental Management\n- **ISO 45001**: Occupational Health & Safety\nThese certifications ensure that DSV operates to the highest international standards in safety, service quality, and environmental responsibility."
        })

    if match([r"\bgdp\b", r"what is gdp", r"gdp warehouse", r"gdp compliant", r"gdp certified"]):
        return jsonify({"reply":
            "GDP stands for **Good Distribution Practice**, a quality standard for warehouse and transport operations of pharmaceutical products. DSV’s healthcare storage facilities in Abu Dhabi, including the Airport Freezone warehouse, are GDP-compliant, ensuring cold chain integrity, traceability, and regulatory compliance."
        })

    if match([r"cold chain", r"what.*cold chain", r"cold storage", r"temperature zones", r"what.*chains.*temperature", r"freezer room", r"cold room", r"ambient storage"]):
        return jsonify({"reply":
            "DSV offers full temperature-controlled logistics including:\n\n"
            "🟢 **Ambient Storage**: +18°C to +25°C (for general FMCG, electronics, and dry goods)\n"
            "🔵 **Cold Room**: +2°C to +8°C (for pharmaceuticals, healthcare, and food products)\n"
            "🔴 **Freezer Room**: –22°C (for frozen goods and sensitive biological materials)\n\n"
            "Our warehouses in Abu Dhabi are equipped with temperature monitoring, backup power, and GDP-compliant systems to maintain cold chain integrity."
        })

    if match([r"\brms\b", r"record management system", r"document storage", r"storage of files", r"paper storage"]):
        return jsonify({"reply":
            "RMS (Record Management System) at DSV is located inside the 21K warehouse in Mussafah. It is used to store and manage physical documents, archives, and secure records for clients like Civil Defense.\n\n"
            "The RMS area is equipped with an **FM 200 fire suppression system** for safe document protection. Note: RMS is not used for storing Return Material."
        })

    if match([r"quote.*asset", r"quotation.*asset management", r"what.*collect.*client.*asset", r"info.*for.*asset.*quotation"]):
        return jsonify({"reply":
            "To prepare an **Asset Management** quotation, collect the following from the client:\n"
            "1️⃣ Type of assets (IT, furniture, tools, etc.)\n"
            "2️⃣ Quantity and tagging type (barcode or RFID)\n"
            "3️⃣ Duration of storage or tracking\n"
            "4️⃣ Reporting/system integration needs\n"
            "5️⃣ Any relocation, retrieval, or disposal cycles"
        })
    if match([r"^rfid$", r"what is rfid", r"rfid meaning", r"rfid technology"]):
        return jsonify({"reply":
        "**RFID** stands for *Radio Frequency Identification*. It’s a technology that uses radio waves to automatically identify and track tags attached to objects.\n\n"
        "At **DSV Abu Dhabi**, RFID is used for:\n"
        "- Asset tracking and management\n"
        "- Warehouse inventory visibility\n"
        "- Automated gate control and access logging\n\n"
        "RFID tags can be passive (no battery) or active (battery-powered) and can be scanned without direct line of sight."
    })

    if match([r"asset management", r"what is asset management", r"tracking of assets", r"rfid.*asset"]):
        return jsonify({"reply":
            "DSV offers complete **Asset Management** solutions including:\n- Barcode or RFID tracking\n- Asset labeling\n- Storage and life-cycle monitoring\n- Secure location control\n\nIdeal for IT equipment, tools, calibration items, and government assets."
        })

    if match([
        r"asset labeling", r"asset labelling", r"label assets", r"tagging assets",
        r"rfid tagging", r"barcode tagging", r"labeling", r"labelling",
        r"what is labeling", r"labeling service", r"labeling support",
        r"label.*asset", r"asset.*tag"
    ]):
        return jsonify({"reply":
            "DSV provides **Asset Labeling** services using RFID or barcode tags. Labels include:\n"
            "- Unique ID numbers\n"
            "- Ownership info\n"
            "- Scannable codes for inventory and asset tracking\n\n"
            "These labels are applied during intake or on-site at the client’s location upon request."
        })

    if match([
        r"\brack\b", r"\bracks\b", r"warehouse rack", r"warehouse racks", r"rack types",
        r"types of racks", r"racking system", r"rack system", r"racking layout", r"rack height",
        r"rack.*info", r"rack.*design", r"21k.*rack", r"rack.*21k", r"pallet levels"
    ]):
        return jsonify({"reply":
            "The 21K warehouse in Mussafah uses 3 racking systems:\n\n"
            "🔷 **Selective Racking**:\n- Aisle width: 2.95m–3.3m\n- Standard access to all pallets\n\n"
            "🔷 **VNA (Very Narrow Aisle)**:\n- Aisle width: 1.95m\n- High-density storage with specialized forklifts\n\n"
            "🔷 **Drive-in Racking**:\n- Aisle width: 2.0m\n- Deep storage for uniform SKUs\n\n"
            "All racks are **12 meters tall** with **6 pallet levels plus ground**.\n"
            "Each bay holds:\n- **14 Standard pallets** (1.2m × 1.0m)\n"
            "- **21 Euro pallets** (1.2m × 0.8m)"
        })

    if match([
        r"pallet positions", r"pallet position", r"how many.*pallet.*position", r"pallet slots",
        r"positions per bay", r"rack.*pallet.*position", r"warehouse pallet capacity"
    ]):
        return jsonify({"reply":
            "Each rack bay in the 21K warehouse has:\n"
            "- **6 pallet levels** plus ground\n"
            "- Fits **14 Standard pallets** or **21 Euro pallets** per bay\n\n"
            "Across the facility, DSV offers thousands of pallet positions for ambient, VNA, and selective racking layouts. The exact total depends on rack type and client configuration."
        })

    if match([
        r"\baisle\b", r"aisle width", r"width of aisle", r"aisles", r"warehouse aisle",
        r"vna aisle", r"how wide.*aisle", r"rack aisle width"
    ]):
        return jsonify({"reply":
            "Here are the aisle widths used in DSV’s 21K warehouse:\n\n"
            "🔹 **Selective Racking**: 2.95m – 3.3m\n"
            "🔹 **VNA (Very Narrow Aisle)**: 1.95m\n"
            "🔹 **Drive-in Racking**: 2.0m\n\n"
            "These widths are optimized for reach trucks, VNA machines, and efficient space utilization."
        })

    # Short follow-ups like "size", "area", "sqm"
    if match([r"^size$", r"^area$", r"^sqm$", r"^m2$", r"^capacity$"]):
        return jsonify({"reply":
            "📏 **DSV Abu Dhabi warehouse sizes**\n"
            "- 21K (Mussafah): **21,000 sqm** (main site, 7 chambers)\n"
            "- M44: **5,760 sqm**\n"
            "- M45: **5,000 sqm**\n"
            "- Al Markaz: **12,000 sqm**\n"
            "- **Total warehouse space** (Abu Dhabi): ~**44,000 sqm**\n"
            "- **Open yard**: **360,000 sqm**"
        })

    if match([
        r"\barea\b", r"warehouse area", r"warehouses area", r"warehouse size", r"warehouses size",
        r"how big.*warehouse", r"storage area", r"facilities", r"facility", r"warehouses", r"warehouse total sqm", r"warehouse.*dimensions"
    ]):
        return jsonify({"reply":
            "DSV Abu Dhabi has approximately **44,000 sqm** of total warehouse space, distributed as follows:\n"
            "- **21K Warehouse (Mussafah)**: 21,000 sqm\n"
            "- **M44**: 5,760 sqm\n"
            "- **M45**: 5,000 sqm\n"
            "- **Al Markaz (Hameem)**: 12,000 sqm\n\n"
            "Additionally, we have **360,000 sqm** of open yard space, and a total logistics site of **481,000 sqm** including service roads and utilities."
        })

    if match([
        r"warehouse.*space.*available", r"do you have.*warehouse.*space", r"space in warehouse",
        r"any warehouse space", r"warehouse availability", r"available.*storage",
        r"available.*warehouse", r"wh space.*available", r"vacant.*warehouse"
    ]):
        return jsonify({"reply": "For warehouse occupancy, please contact Biju Krishnan at **biju.krishnan@dsv.com**. He’ll assist with availability, allocation, and scheduling a site visit if needed."})

    if match([
        r"\btemp\b", r"temperture", r"temperature", r"temperature zones", r"warehouse temp",
        r"warehouse temperature", r"cold room", r"freezer room", r"ambient temperature",
        r"temp.*zones", r"how cold", r"cold storage", r"temperature range"
    ]):
        return jsonify({"reply":
            "DSV warehouses support three temperature zones:\n\n"
            "🟢 **Ambient Storage**: +18°C to +25°C — for general cargo and FMCG\n"
            "🔵 **Cold Room**: +2°C to +8°C — for food and pharmaceuticals\n"
            "🔴 **Freezer Room**: –22°C — for frozen goods and sensitive materials\n\n"
            "All temperature-controlled areas are monitored 24/7 and GDP-compliant."
        })

    if match([r"chambers.*21k", r"how many.*chambers", r"warehouse.*layout", r"wh.*layout", r"warehouse.*structure", r"\bchambers\b"]):
        return jsonify({"reply": "There are 7 chambers in the 21K warehouse with different sizes and rack types. Chambers range from 1,000–5,000 sqm and together can accommodate ~35,000 CBM."})
    if match([r"packing material", r"what packing material", r"materials used for packing"]):
            return jsonify({"reply":
            "DSV uses high-grade packing materials:\n- Shrink wrap (6 rolls per box, 1 roll = 20 pallets)\n- Strapping rolls + buckle kits (1 roll = 20 pallets)\n- Bubble wrap, carton boxes, foam sheets\n- Heavy-duty pallets (wooden/plastic)\nUsed for relocation, storage, and export."
        })
    if match([
        r"warehouse activities", r"inbound process", r"outbound process", r"wh process",
        r"warehouse process", r"SOP", r"operation process", r"putaway", r"replenishment",
        r"picking", r"packing", r"cycle count", r"warehouse operations", r"warehouse workflow",
        r"\bwh\b.*operation", r"warehouse tasks", r"warehouse flow"
    ]):
        return jsonify({"reply":
            "Typical warehouse processes at DSV include:\n\n"
            "1️⃣ **Inbound**: receiving, inspection, put-away\n"
            "2️⃣ **Storage**: in racks or bulk zones\n"
            "3️⃣ **Order Processing**: picking, packing, labeling\n"
            "4️⃣ **Outbound**: staging, dispatch, transport coordination\n"
            "5️⃣ **Inventory Control**: cycle counting, stock checks, and returns\n\n"
            "Additional activities include VAS (Value Added Services), replenishment, and returns management.\n\n"
            "All operations are fully managed through our INFOR WMS system for visibility, traceability, and efficiency."
        })

    if match([
        r"\bmhe\b", r"mhe equipment", r"material handling equipment",
        r"\bmachineries\b", r"\bmachinery\b", r"machines used",
        r"equipment", r"equipment used", r"warehouse equipment"
    ]):
        return jsonify({"reply":
            "DSV uses a wide range of **Material Handling Equipment (MHE)** for efficient warehouse and yard operations, including:\n\n"
            "- 🚜 Forklifts (3–15T)\n"
            "- 🎯 VNA (Very Narrow Aisle) machines\n"
            "- 🤏 Reach Trucks\n"
            "- 🎯 Pallet Jacks / Hand Pallets\n"
            "- 🏗️ Cranes for heavy lift\n"
            "- 📦 Container Lifters / Strippers\n\n"
            "This equipment supports storage, picking, loading, and transport operations across all DSV Abu Dhabi facilities."
        })

    if match([r"dsv warehouse", r"abu dhabi warehouse", r"warehouse facilities"]):
        return jsonify({"reply": "DSV Abu Dhabi has 44,000 sqm of warehouse space across 21K, M44, M45, and Al Markaz. Main site is 21K in Mussafah (21,000 sqm, 7 chambers)."})

    # --- What is WMS ---
    if match([r"what is wms|wms meaning|warehouse management system"]):
        return jsonify({"reply": "WMS stands for Warehouse Management System. DSV uses INFOR WMS for inventory control, inbound/outbound, and full visibility."})

    if match([r"\binventory\b", r"inventory management", r"what wms system dsv use", r"inventory control", r"inventory system", r"stock tracking"]):
        return jsonify({"reply":
            "DSV uses INFOR WMS to manage all inventory activities. It provides:\n- Real-time stock visibility\n- Bin-level tracking\n- Batch/serial number control\n- Expiry tracking (for pharma/FMCG)\n- Integration with your ERP system"
        })

    if match([r"\binfor\b", r"what is infor", r"infor wms", r"who makes wms", r"infor system", r"infor software"]):
        return jsonify({"reply":
            "INFOR is the software provider of the Warehouse Management System (WMS) used by DSV. "
            "It supports real-time inventory tracking, barcode scanning, inbound/outbound flow, and integration with ERP systems. "
            "INFOR WMS is known for its scalability, accuracy, and user-friendly interface for warehouse operations."
        })

    if match([
        r"\bwarehouse\b", r"\bwarehousing\b", r"warehouse info",
        r"tell me about warehouse", r"warehouse\?"
    ]) and not re.search(r"(area|size|space|temperature|temp|cold|freezer|wms|dsv|location|rack|21k|chamber|operations|facility|facilities)", message):
        return jsonify({"reply": "Can you clarify what aspect of the warehouse you're asking about? Size, temp zones, racking, chambers, or something else?"})

    # --- Open Yard Space Availability ---
    if match([
        r"open yard.*occupancy", r"space.*open yard", r"open yard.*available",
        r"do we have.*open yard", r"open yard availability", r"open yard.*space",
        r"yard capacity", r"yard.*vacancy", r"any.*open yard.*space"
    ]):
        return jsonify({"reply": "For open yard occupancy, please contact Antony Jeyaraj at **antony.jeyaraj@dsv.com**. He can confirm available space and assist with pricing or scheduling a visit."})

    if match([r"\btapa\b", r"tapa certified", r"tapa standard", r"tapa compliance"]):
        return jsonify({"reply":
            "TAPA stands for Transported Asset Protection Association. It’s a global security standard for the safe handling, warehousing, and transportation of high-value goods. DSV follows TAPA-aligned practices for secure transport and facility operations, including access control, CCTV, sealed trailer loading, and secured parking."
        })

    if match([r"freezone", r"free zone", r"abu dhabi freezone", r"airport freezone", r"freezone warehouse"]):
        return jsonify({"reply":
            "DSV operates a GDP-compliant warehouse in the **Abu Dhabi Airport Freezone**, specialized in pharmaceutical and healthcare logistics. It offers:\n"
            "- Temperature-controlled and cold chain storage\n"
            "- Customs-cleared import/export operations\n"
            "- Proximity to air cargo terminals\n"
            "- Full WMS and track-and-trace integration\n"
            "This setup supports fast, regulated distribution across the UAE and GCC."
        })

    if match([r"\bqhse\b", r"quality health safety environment", r"qhse policy", r"qhse standards", r"dsv qhse"]):
        return jsonify({"reply":
            "DSV follows strict QHSE standards across all operations. This includes:\n"
            "- Quality checks (ISO 9001)\n"
            "- Health & safety compliance (ISO 45001)\n"
            "- Environmental management (ISO 14001)\n"
            "All staff undergo QHSE training, and warehouses are equipped with emergency protocols, access control, firefighting systems, and first-aid kits."
        })

    if match([r"\bhse\b", r"health safety environment", r"dsv hse", r"hse policy", r"hse training"]):
        return jsonify({"reply":
            "DSV places strong emphasis on HSE compliance. We implement:\n"
            "- Safety inductions and daily toolbox talks\n"
            "- Fire drills and emergency response training\n"
            "- PPE usage and incident reporting procedures\n"
            "- Certified HSE officers across sites\n"
            "We’re committed to zero harm in the workplace."
        })

    if match([r"training", r"staff training", r"employee training", r"warehouse training", r"qhse training"]):
        return jsonify({"reply":
            "All DSV warehouse and transport staff undergo structured training programs, including:\n"
            "- QHSE training (Safety, Fire, First Aid)\n"
            "- Equipment handling (Forklifts, Cranes, VNA)\n"
            "- WMS and inventory systems\n"
            "- Customer service and operational SOPs\n"
            "Regular refresher courses are also conducted."
        })

    if match([r"\bdg\b", r"dangerous goods", r"hazardous material", r"hazmat", r"hazard class", r"dg storage"]):
        return jsonify({"reply":
            "Yes, DSV handles **DG (Dangerous Goods)** and hazardous materials in specialized chemical storage areas. We comply with all safety and documentation requirements including:\n"
            "- Hazard classification and labeling\n"
            "- MSDS (Material Safety Data Sheet) submission\n"
            "- Trained staff for chemical handling\n"
            "- Temperature-controlled and fire-protected zones\n"
            "- Secure access and emergency systems\n\n"
            "Please note: For a DG quotation, we require the **material name, hazard class, CBM, period, and MSDS**."
        })

    # --- Chamber Mapping ---
    if match([r"ch2|chamber 2"]):
        return jsonify({"reply": "Chamber 2 is used by PSN (Federal Authority of Protocol and Strategic Narrative)."})

    if match([r"ch3|chamber 3"]):
        return jsonify({"reply": "Chamber 3 is used by food clients and fast-moving items."})

    # --- Chamber Mapping (Unified) ---
# Place inside the chat() function and fix scope
    if match([r"\bch\d+\b", r"chamber\s*\d+", r"who.*in.*ch\d+", r"who.*in.*chamber\s*\d+"]):
        ch_num = re.search(r"ch(?:amber)?\s*(\d+)", message)
        clients = {
        1: "Khalifa University",
        2: "PSN (Federal Authority of Protocol and Strategic Narrative)",
        3: "Food clients & fast-moving items",
        4: "MCC, TR, and ADNOC",
        5: "PSN",
        6: "ZARA & TR",
        7: "Civil Defense and the RMS",}
        if ch_num:
            chamber = int(ch_num.group(1))
            if chamber in clients:
                return jsonify({"reply": f"Chamber {chamber} is occupied by {clients[chamber]}."})
            else:
                return jsonify({"reply": f"I don't have data for Chamber {chamber}."})

    # --- Warehouse Occupancy (short) ---
    if match([r"warehouse occupancy|occupancy|space available|any space in warehouse|availability.*storage"]):
        return jsonify({"reply": "For warehouse occupancy, contact Biju Krishnan at biju.krishnan@dsv.com."})

    if match([r"open yard.*occupancy|yard space.*available|yard capacity|yard.*availability"]):
        return jsonify({"reply": "For open yard occupancy, contact Antony Jeyaraj at antony.jeyaraj@dsv.com."})

    # --- Industry: Retail & Fashion ---
    if match([r"\bretail\b", r"fashion and retail", r"fashion logistics", r"retail supply chain"]):
        return jsonify({"reply":
            "DSV provides tailored logistics solutions for the **retail and fashion industry**, including:\n- Warehousing (racked, ambient, VNA)\n- Inbound & outbound transport\n- Value Added Services (labeling, repacking, tagging)\n- Last-mile delivery to malls and retail stores\n- WMS integration for real-time visibility"
        })

    # --- Industry: Oil & Gas ---
    if match([r"oil and gas", r"oil & gas", r"\bo&g\b", r"energy sector", r"oil logistics"]):
        return jsonify({"reply":
            "DSV supports the **Oil & Gas industry** across Abu Dhabi and the GCC through:\n"
            "- Storage of chemicals and DG\n"
            "- Heavy equipment transport\n"
            "- 3PL/4PL project logistics\n"
            "- ADNOC-compliant warehousing and safety\n"
            "- Support for offshore & EPC contractors with specialized fleet"
        })

    if match([
        r"heavy lift", r"heavy lift logistics", r"heavy cargo project", r"oversized transport", r"lifting heavy cargo",
        r"heavy project cargo", r"lift.*heavy", r"project transport.*heavy", r"transport.*heavy equipment"
    ]):
        return jsonify({"reply":
            "Yes, DSV handles **heavy lift logistics** across the UAE and GCC. We provide:\n\n"
            "- 🏗 Mobile cranes (up to 80T)\n"
            "- 🚛 Lowbed trailers for oversized cargo\n"
            "- 📦 Rigging, lifting, and permit coordination\n"
            "- 🛣 Route planning for abnormal loads\n"
            "- 📋 QHSE-compliant execution\n\n"
            "Examples include transformer lifts, construction machinery, and ADNOC EPC project deliveries."
        })

    if match([r"breakbulk", r"break bulk", r"heavy cargo", r"non-containerized cargo"]):
        return jsonify({"reply":
            "DSV handles **breakbulk and heavy logistics** including:\n- Oversized cargo (machinery, steel, transformers)\n- Lowbed trailer and crane support\n- Project logistics & site delivery\n- DG compliance and route planning\n- Full UAE & GCC transport coordination"
        })

    if match([r"last mile", r"last mile delivery", r"final mile", r"city delivery"]):
        return jsonify({"reply":
            "DSV offers **last-mile delivery** services across the UAE using small city trucks and vans. These are ideal for e-commerce, retail, and healthcare shipments requiring fast and secure delivery to final destinations. Deliveries are WMS-tracked and coordinated by our OCC team for full visibility."
        })

    if match([r"cross dock", r"cross docking", r"cross-dock", r"crossdock facility"]):
        return jsonify({"reply":
            "Yes, DSV supports **cross-docking** for fast-moving cargo:\n- Receive → Sort → Dispatch (no storage)\n- Ideal for FMCG, e-commerce, and retail\n- Reduces lead time and handling\n- Available at Mussafah and KIZAD hubs"
        })

    if match([
    r"transit\b", r"transit store", r"transit warehouse", r"transit storage", 
    r"temporary storage", r"short term storage"]):
        return jsonify({"reply":
        "DSV offers **transit storage** for short-term cargo holding. Ideal for:\n"
        "- Customs-cleared goods awaiting dispatch\n"
        "- Re-export shipments\n"
        "- Short-duration contracts\n"
        "Options available in Mussafah, Airport Freezone, and KIZAD."
    })

    # --- EV trucks ---
    if match([r"ev truck|electric vehicle|zero emission|sustainable transport"]):
        return jsonify({"reply": "DSV Abu Dhabi operates EV trucks hauling 40ft containers. Each has ~250–300 km range and supports port shuttles & green logistics."})

    # --- DSV Managing Director (MD) ---
    if match([r"\bmd\b|managing director|head of dsv|ceo|boss of dsv|hossam mahmoud"]):
        return jsonify({"reply": "Mr. Hossam Mahmoud is the Managing Director, Road & Solutions and CEO Abu Dhabi. He oversees all logistics, warehousing, and transport operations in the region."})

    # --- Services DSV Provides ---
    if match([
        r"what.*service[s]?.*dsv.*provide",
        r"what (do|does).*dsv.*do",
        r"what.*they.*do",
        r"what.*they.*provide",
        r"what (do|does).*they.*do",
        r"what (do|does).*they.*offer",
        r"what.*service[s]?.*they.*provide",
        r"dsv.*offer",
        r"dsv.*specialize",
        r"dsv.*work",
        r"dsv.*services",
        r"what.*type.*service",
        r"type.*of.*logistics",
        r"services.*dsv",
        r"what.*dsv.*do",
        r"dsv.*offerings"
    ]):
        return jsonify({"reply":
            "**DSV Abu Dhabi** provides full logistics and supply chain services, including:\n\n"
            "🚚 **2PL** – Road transport, containers, last-mile delivery\n"
            "🏢 **3PL** – Warehousing, inventory, VAS, WMS\n"
            "🔗 **3.5PL** – Hybrid logistics (execution + partial strategy)\n"
            "🧠 **4PL** – Fully managed supply chain operations\n\n"
            "**Main Facilities:**\n"
            "- 📍 **21K Warehouse (Mussafah)** – 21,000 sqm, 7 chambers\n"
            "- 📍 **M44 / M45** – Sub-warehouses in Mussafah\n"
            "- 📍 **Al Markaz (Hameem)** – 12,000 sqm\n"
            "- 📍 **KIZAD** – 360,000 sqm open yard\n"
            "- 📍 **Airport Freezone** – GDP-compliant storage for healthcare\n\n"
            "📞 +971 2 555 2900 | 🌐 dsv.com"
        })

    if match([r"dsv abu dhabi", r"about dsv abu dhabi", r"who is dsv abu dhabi", r"what is dsv abu dhabi", r"dsv in abu dhabi"]):
        return jsonify({"reply":
            "DSV Abu Dhabi offers full logistics, warehousing, and transport services. Our main operations include:\n\n"
            "📍 **21K Warehouse (Mussafah)** – 21,000 sqm, 15m height, 7 chambers\n"
            "📍 **M44 & M45 Sub-warehouses** – 5,760 sqm & 5,000 sqm\n"
            "📍 **Al Markaz (Hameem)** – 12,000 sqm\n"
            "📍 **KIZAD Open Yard** – 360,000 sqm\n"
            "📍 **Airport Freezone** – Pharma & healthcare storage\n\n"
            "We handle 2PL, 3PL, 4PL logistics, WMS, VAS, and temperature-controlled storage. Contact +971 2 555 2900 or visit dsv.com."
        })
# --- General Logistics Overview ---
    if (
        match([
        r"\blogistics\b",
        r"what.*is.*logistics",
        r"about logistics",
        r"logistics info",
        r"tell me about logistics",
        r"explain logistics",
        r"what do you know about logistics",
        r"logistics overview",
        r"define logistics",
        r"logistics meaning"
    ])
    # don't trigger if user is asking about 1PL/2PL/3PL/3.5PL/4PL/5PL/6PL
        and not re.search(
        r"\b(1|2|3|3\.5|4|5|6)pl\b|\b(first|second|third|fourth|fifth|sixth)\s+party\s+logistics\b",
        message
    )
):
        return jsonify({"reply": """**Logistics** refers to the planning, execution, and management of the movement and storage of goods, services, and information from origin to destination.
At **DSV Abu Dhabi**, logistics includes:
- 📦 **Warehousing** – AC, Non-AC, Open Yard, and temperature-controlled facilities
- 🚛 **Transportation** – Local & GCC trucking (flatbeds, reefers, lowbeds, box trucks, double trailers, etc.)
- 🧾 **Value Added Services** – Packing, labeling, inventory counts, kitting & assembly
- 🌍 **Global Freight Forwarding** – Air, sea, and multimodal shipments
- 🧠 **4PL & Supply Chain Solutions** – End-to-end management, optimization, and consulting
We manage everything from port-to-door, ensuring safety, compliance, and cost efficiency."""})


    # --- DSV Vision / Mission ---
    if match([
        r"dsv vision", r"what is dsv vision", r"dsv mission", r"dsv mission and vision",
        r"company vision", r"company mission", r"mission statement", r"vision statement", r"vision of dsv"
    ]):
        return jsonify({"reply":
            "**DSV’s Vision & Mission:**\n\n"
            "🌍 **Vision:** To be a leading global supplier of transport and logistics services, meeting our customers’ needs for quality, service, and reliability.\n\n"
            "🚀 **Mission:** We aim to deliver superior customer experiences by providing integrated logistics solutions that add value and efficiency across the supply chain.\n\n"
            "♻️ **Sustainability Vision:** DSV is committed to reducing CO₂ emissions and achieving net-zero by 2050 through:\n"
            "- Electric vehicle transport\n"
            "- Solar-powered warehouses\n"
            "- Route optimization & consolidation\n"
            "- Environmental compliance (ISO 14001)\n\n"
            "Visit dsv.com to learn more about our global goals and ESG initiatives."
        })

    if not re.search(r"(wms|warehouse management|abu dhabi|fleet|transport|vision|mission|location|address|site|service)", message) and match([
        r"\bdsv\b", r"about dsv", r"who is dsv", r"what is dsv",
        r"dsv info", r"tell me about dsv", r"dsv overview",
        r"dsv abbreviation", r"dsv stands for", r"what does dsv mean"
    ]):
        return jsonify({"reply":
            "DSV stands for **'De Sammensluttede Vognmænd'**, meaning **'The Consolidated Hauliers'** in Danish. "
            "Founded in 1976, DSV is a global logistics leader operating in over 80 countries."
        })

    if match([
        r"sustainability", r"green logistics", r"sustainable practices", r"environmental policy",
        r"carbon footprint", r"eco friendly", r"zero emission goal", r"environment commitment"
    ]):
        return jsonify({"reply":
            "DSV is committed to **sustainability and reducing its environmental footprint** across all operations. Initiatives include:\n"
            "- Transition to **electric vehicles (EV)** for last-mile and container transport\n"
            "- Use of **solar energy** and energy-efficient warehouse lighting\n"
            "- Consolidated shipments to reduce CO₂ emissions\n"
            "- Compliance with **ISO 14001** (Environmental Management)\n"
            "- Green initiatives in packaging, recycling, and process optimization\n\n"
            "DSV’s global strategy aligns with the UN Sustainable Development Goals and aims for net-zero emissions by 2050."
        })

    # --- Industry Tags ---
    if match([r"\bfmcg\b|fast moving|consumer goods"]):
        return jsonify({"reply": "FMCG stands for (Fast-Moving Consumer Goods) DSV provides fast turnaround warehousing for FMCG clients including dedicated racking, SKU control, and high-frequency dispatch."})

    if match([r"insurance|is insurance included|cargo insurance"]):
        return jsonify({"reply": "Insurance is not included by default in quotations. It can be arranged separately upon request."})

    # --- Lean Six Sigma ---
    if match([r"lean six sigma|warehouse improvement|continuous improvement|kaizen|process efficiency|6 sigma|warehouse process improvement|lean method"]):
        return jsonify({"reply": "DSV applies Lean Six Sigma principles in warehouse design and process flow to reduce waste, improve accuracy, and maximize efficiency. We implement 5S, KPI dashboards, and root-cause analysis for continuous improvement."})

    # --- Warehouse Activities (alt paths) ---
    if match([r"warehouse temp|temperature.*zone|storage temperature|cold room|freezer|ambient temp|warehouse temperature"]):
        return jsonify({"reply": "DSV provides 3 temperature zones:\n- **Ambient**: +18°C to +25°C\n- **Cold Room**: +2°C to +8°C\n- **Freezer**: –22°C\nThese zones are used for FMCG, pharmaceuticals, and temperature-sensitive products."})

    if match([r"size of our warehouse|total warehouse area|total sqm|warehouse size|how big.*warehouse"]):
        return jsonify({"reply": "DSV Abu Dhabi has approx. **44,000 sqm** of warehouse space:\n- 21K in Mussafah (21,000 sqm)\n- M44 (5,760 sqm)\n- M45 (5,000 sqm)\n- Al Markaz in Hameem (12,000 sqm)\nPlus 360,000 sqm of open yard."})

    if match([r"kitting", r"assembly", r"kitting and assembly", r"value added kitting"]):
        return jsonify({"reply":
            "DSV provides **kitting and assembly** as a Value Added Service:\n- Combine multiple SKUs into kits\n- Light assembly of components\n- Repacking and labeling\n- Ideal for retail, pharma, and project logistics"
        })

    if match([r"\brelocation\b", r"move warehouse", r"shift cargo", r"site relocation"]):
        return jsonify({"reply":
            "Yes, DSV provides full **relocation services**:\n- Machinery shifting\n- Office and warehouse relocations\n- Packing, transport, offloading\n- Insurance and dismantling available\nHandled by our trained team with all safety measures."
        })

    if match([r"machinery|machineries|machines used|equipment|equipment used"]):
        return jsonify({"reply": "DSV uses forklifts (3–15T), VNA, reach trucks, pallet jacks, cranes, and container lifters in warehouse and yard operations."})

    if match([r"pallet.*bay|how many.*bay.*pallet", r"bay.*standard pallet", r"bay.*euro pallet"]):
        return jsonify({"reply": "Each bay in 21K can accommodate 14 Standard pallets or 21 Euro pallets. This layout maximizes efficiency for various cargo sizes."})

    # --- Ecom / Insurance / WMS (alt) ---
    if match([r"ecommerce|e-commerce|online retail|ecom|dsv online|shop logistics|online order|fulfillment center"]):
        return jsonify({"reply":
            "DSV provides end-to-end e-commerce logistics including warehousing, order fulfillment, pick & pack, returns handling, last-mile delivery, and integration with Shopify, Magento, and custom APIs. Our Autostore and WMS systems enable fast, accurate processing of online orders from our UAE hubs including KIZAD and Airport Freezone."
        })

    if match([r"insurance|cargo insurance|storage insurance|are items insured"]):
        return jsonify({"reply": "Insurance is not included by default in DSV storage or transport quotes. It can be arranged upon client request, and is subject to cargo value, category, and terms agreed."})

    if match([
        r"\bwms\b",
        r"what.*wms.*system.*dsv.*use",
        r"what wms system dsv use",
        r"what wms system",
        r"dsv.*wms.*system",
        r"which.*wms.*system",
        r"wms.*used.*by.*dsv",
        r"wms.*software.*dsv",
        r"inventory.*tracking.*system",
        r"dsv.*inventory.*system",
        r"what is wms"
    ]):
        return jsonify({"reply": "DSV uses the **INFOR Warehouse Management System (WMS)** to manage inventory, inbound/outbound flows, and order tracking. It supports real-time dashboards, barcode scanning, and integrates with client ERP systems."})

    if match([r"warehouse activities|warehouse tasks|daily warehouse work"]):
        return jsonify({"reply": "DSV warehouse activities include receiving (inbound), put-away, storage, replenishment, order picking, packing, staging, and outbound dispatch. We also handle inventory audits, cycle counts, and VAS."})

    if match([r"\bsop\b", r"standard operating procedure", r"standard operation process"]):
        return jsonify({"reply":
            "SOP stands for **Standard Operating Procedure**. It refers to detailed, written instructions to achieve uniformity in operations. "
            "DSV maintains SOPs for all warehouse, transport, and VAS processes to ensure safety, compliance, and efficiency."
        })

    # --- Air & Sea Services ---
    if match([
        r"air and sea", r"sea and air", r"air & sea", r"air freight and sea freight",
        r"dsv air and sea", r"dsv sea and air", r"dsv air & sea", r"air ocean", r"air & ocean"
    ]):
        return jsonify({"reply":
            "DSV provides comprehensive **Air & Sea freight forwarding** services globally and in the UAE:\n\n"
            "✈️ **Air Freight**:\n"
            "- Express, standard, and consolidated options\n"
            "- Charter solutions for urgent cargo\n"
            "- Abu Dhabi Airport Freezone warehouse integration\n\n"
            "🚢 **Sea Freight**:\n"
            "- Full Container Load (FCL) and Less than Container Load (LCL)\n"
            "- Customs clearance and documentation support\n"
            "- Direct access to UAE ports via Jebel Ali, Khalifa, and Zayed Port\n\n"
            "Our team handles end-to-end transport, consolidation, and global forwarding through DSV’s global network."
        })

    # --- Chemical quotation ---
    if match([
        r"what.*(need|have).*collect.*chemical.*quote",
        r"what.*(to|do).*collect.*chemical.*quotation",
        r"build.*up.*chemical.*quote", r"build.*chemical.*quote",
        r"make.*chemical.*quotation", r"prepare.*chemical.*quote",
        r"chemical.*quote.*requirements", r"requirements.*chemical.*quote",
        r"info.*for.*chemical.*quote", r"details.*for.*chemical.*quotation",
        r"what.*required.*chemical.*quotation", r"quotation.*chemical.*details"
    ]):
        return jsonify({"reply":
            "To provide a quotation for **chemical storage**, please collect the following from the client:\n"
            "1️⃣ **Product Name & Type**\n"
            "2️⃣ **Hazard Class / Classification**\n"
            "3️⃣ **Required Volume (CBM/SQM)**\n"
            "4️⃣ **Storage Duration (contract period)**\n"
            "5️⃣ **MSDS** – Material Safety Data Sheet\n"
            "6️⃣ **Any special handling or packaging needs**"
        })

    if match([r"store.*chemical|quotation.*chemical|data.*chemical|requirement.*chemical"]):
        return jsonify({"reply": "To quote for chemical storage, we need:\n- Material name\n- Hazard class\n- CBM\n- Period\n- MSDS (Material Safety Data Sheet)."})

    if match([r"\bmsds\b|material safety data sheet|chemical data"]):
        return jsonify({"reply": "Yes, MSDS (Material Safety Data Sheet) is mandatory for any chemical storage inquiry. It ensures safe handling and classification of the materials stored in DSV’s facilities."})

    if match([r"quote.*chemical.*warehouse", r"quote.*chemical storage", r"quote.*any storage", r"what.*need.*quote.*storage", r"build.*quote.*chemical"]):
        return jsonify({"reply":
            "To build a quotation for storage (especially chemical), collect the following:\n"
            "1️⃣ Type of material / hazard class\n"
            "2️⃣ Volume (CBM or SQM)\n"
            "3️⃣ Storage duration (contract period)\n"
            "4️⃣ MSDS if chemical\n"
            "5️⃣ Handling frequency (throughput)\n\n"
            "Once ready, please fill the form on the left."
        })

    # --- SQM↔CBM Helper ---
    if match([
        r"(how|what).*convert.*(sqm|sq\.?m).*cbm",
        r"(convert|calculate|estimate).*cbm.*(from|using).*sqm",
        r"(sqm|sq\.?m).*to.*cbm",
        r"cbm.*(from|based on|calculated from).*sqm",
        r"(client|customer).*only.*(sqm|sq\.?m)",
        r"(only|just).*sqm.*not.*cbm",
        r"no.*cbm.*(provided|available)",
        r"given.*sqm.*want.*cbm",
        r"client.*gave.*sqm.*how.*cbm",
        r"how.*cbm.*(if|when).*client.*(gives|provides).*sqm",
        r"i have.*sqm.*need.*cbm"
    ]):
        return jsonify({"reply": "If the client doesn’t provide CBM, you can estimate it using the rule: **1 SQM ≈ 1.8 CBM** for standard racked warehouse storage."})

    if match([
        r"(what.*collect.*client.*quotation)", r"(what.*info.*client.*quote)",
        r"(quotation.*requirements)", r"(quotation.*information.*client)",
        r"(details.*for.*quotation)", r"(build.*quotation.*info)",
        r"(prepare.*quotation.*client)", r"(required.*info.*quote)"
    ]):
        return jsonify({"reply":
            "To build a proper 3PL storage quotation, please collect the following information from the client:\n"
            "1️⃣ **Type of Commodity** – What items are being stored (FMCG, chemical, pharma, etc.)\n"
            "2️⃣ **Contract Period** – Expected duration of the agreement (in months or years)\n"
            "3️⃣ **Storage Volume** – In CBM/day, CBM/month, or CBM/year for warehousing; in SQM for open yard\n"
            "4️⃣ **Throughput Volumes (IN/OUT)** – Daily or monthly volume in CBM to determine handling pattern and frequency\n\n"
            "Once these details are available, you can proceed to fill the main form to generate a quotation."
        })

    if match([r"proposal|quotation|offer|quote.*open yard|proposal.*open yard|send me.*quote|how to get quote|need.*quotation"]):
        return jsonify({"reply": "To get a full quotation, please close this chat and fill the details in the main form on the left. The system will generate a downloadable document for you."})

    # ===== Compare only requested PLs (1PL/2PL/3PL/3.5PL/4PL/5PL/6PL) =====
    # ===== Compare only requested PLs (1PL/2PL/3PL/3.5PL/4PL/5PL/6PL) =====
    def _extract_pl_mentions(msg: str):
        aliases = {
            "1PL": [r"\b1pl\b", r"\bfirst party logistics\b"],
            "2PL": [r"\b2pl\b", r"\bsecond party logistics\b"],
            "3PL": [r"\b3pl\b", r"\bthird party logistics\b"],
            "3.5PL": [r"\b3\.?5pl\b", r"\bthree and half pl\b", r"\b3pl plus\b", r"\bmiddle of 3pl and 4pl\b"],
            "4PL": [r"\b4pl\b", r"\bfourth party logistics\b"],
            "5PL": [r"\b5pl\b", r"\bfifth party logistics\b"],
            "6PL": [r"\b6pl\b", r"\bsixth party logistics\b"],
        }
        found = []
        for code, pats in aliases.items():
            pos = None
            for p in pats:
                m = re.search(p, msg)
                if m:
                    pos = m.start() if pos is None else min(pos, m.start())
            if pos is not None:
                found.append((code, pos))
        found.sort(key=lambda x: x[1])
        ordered = []
        for code, _ in found:
            if code not in ordered:
                ordered.append(code)
        return ordered

    _PL_DEF = {
        "1PL": {"title": "1PL — First-Party Logistics", "bullets": [
            "Owner of goods does everything in-house (warehouse, trucks, staff, systems).",
            "Max control, but higher CAPEX/OPEX and expertise needed."
        ]},
        "2PL": {"title": "2PL — Second-Party Logistics", "bullets": [
            "Asset/capacity provider (trucks, space, vessels). Client still runs operations.",
            "You rent capacity; processes and planning stay with you."
        ]},
        "3PL": {"title": "3PL — Third-Party Logistics", "bullets": [
            "Outsourced execution: warehousing, transport, order fulfillment, VAS.",
            "Provider runs ops under your strategy/KPIs; WMS/TMS operated by provider."
        ]},
        "3.5PL": {"title": "3.5PL — Hybrid (between 3PL & 4PL)", "bullets": [
            "Provider handles operations + some planning/analytics/CI.",
            "More orchestration than 3PL, not a full lead-logistics role."
        ]},
        "4PL": {"title": "4PL — Fourth-Party Logistics", "bullets": [
            "Lead logistics integrator managing multiple 3PLs/carriers, network design, and strategy.",
            "Single point of contact; end-to-end governance and optimization."
        ]},
        "5PL": {"title": "5PL — Fifth-Party Logistics", "bullets": [
            "Orchestrates networks-of-networks via platforms; heavy data & automation.",
            "Outcome-based management across several 3PL/4PL providers."
        ]},
        "6PL": {"title": "6PL — Sixth-Party Logistics (emerging)", "bullets": [
            "AI-driven/autonomous orchestration (digital twins, predictive planning, autonomous assets).",
            "Vision/early adoption rather than a widely standardised operating model."
        ]},
    }

    def _short_contrast(pls):
        order = ["1PL","2PL","3PL","3.5PL","4PL","5PL","6PL"]
        rank = {k:i for i,k in enumerate(order)}
        pls_sorted = sorted(pls, key=lambda k: rank.get(k, 99))
        parts = []
        for k in pls_sorted:
            if k == "1PL": parts.append("client in-house")
            elif k == "2PL": parts.append("provider assets only")
            elif k == "3PL": parts.append("provider runs execution")
            elif k == "3.5PL": parts.append("exec + some strategy")
            elif k == "4PL": parts.append("lead-logistics orchestration")
            elif k == "5PL": parts.append("platform multi-network")
            elif k == "6PL": parts.append("autonomous/AI orchestration")
        return " → ".join(parts)

    # Compare ONLY requested PLs
    if (
        re.search(r"\b(vs|versus|difference|different|compare|comparison|diff)\b", message)
        and len(_extract_pl_mentions(message)) >= 2
    ):
        asked = _extract_pl_mentions(message)
        lines = ["**Comparison — " + " vs ".join(asked) + "**\n"]
        for code in asked:
            d = _PL_DEF.get(code)
            if not d:
                continue
            lines.append(f"🔹 **{d['title']}**")
            for b in d["bullets"]:
                lines.append(f"- {b}")
            lines.append("")
        lines.append(f"**In short:** {_short_contrast(asked)}.")
        return jsonify({"reply": "\n".join(lines)})

    # --- Service definitions ---
    if match([r"\bwhat is 2pl\b", r"\b2pl\b", r"second party logistics", r"2pl meaning"]):
        return jsonify({"reply":
            "**2PL (Second Party Logistics)** means the customer rents or leases a warehouse or yard facility, but operates it entirely on their own.\n\n"
            "- DSV provides only the **infrastructure** (space, utilities, security)\n"
            "- The client uses their **own manpower, MHE, WMS, and processes**\n"
            "- DSV does **not** get involved in the daily operations\n\n"
            "This is commonly used by clients who want full control over their logistics operations, but need a compliant facility with strategic location."
        })

    if match([r"\bwhat is 3pl\b", r"\b3pl\b", r"third party logistics"]):
        return jsonify({"reply": "3PL (Third Party Logistics) involves outsourcing logistics operations such as warehousing, transportation, picking/packing, and order fulfillment to a provider like DSV."})

    if match([r"\bwhat is 4pl\b", r"\b4pl\b", r"fourth party logistics"]):
        return jsonify({"reply": "4PL (Fourth Party Logistics) is a fully integrated supply chain solution where DSV manages all logistics operations, partners, systems, and strategy on behalf of the client. DSV acts as a single point of contact and coordination."})

    if match([r"\bwhat is 3.5pl\b", r"\b3.5pl\b", r"three and half pl", r"3pl plus", r"middle of 3pl and 4pl"]):
        return jsonify({"reply":
            "3.5PL is an emerging term referring to a hybrid between **3PL and 4PL**:\n- DSV provides operational execution like a 3PL\n- And partial strategic control like a 4PL\nIdeal for clients wanting control with partial outsourcing."
        })

    if match([r"\b5pl\b", r"\bfifth party logistics\b", r"what is 5pl", r"5pl meaning", r"explain 5pl"]):
        return jsonify({"reply":
            "5PL (Fifth Party Logistics) refers to a provider that **manages the entire supply chain network** on behalf of the client, including multiple 3PL/4PL providers.\n\n"
            "It focuses on **complete strategic orchestration** of logistics using data-driven platforms, AI, automation, and integrated digital ecosystems.\n\n"
            "5PL is ideal for businesses needing full end-to-end digital control across multiple logistics layers, particularly in global e-commerce or high-volume industries."
        })

    if match([r"\b6pl\b", r"\bsixth party logistics\b", r"what is 6pl", r"explain 6pl", r"6pl meaning", r"define 6pl"]):
        return jsonify({"reply":
            "**6PL (Sixth Party Logistics)** is an **emerging concept** in supply chain strategy.\n\n"
            "It refers to a logistics model that integrates:\n"
            "- AI-based decision making\n"
            "- Big data analytics\n"
            "- Autonomous systems\n"
            "- Full digital orchestration across 3PL/4PL/5PL layers\n\n"
            "📌 *It's not yet widely adopted but represents the future of smart, fully automated logistics.*"
        })

    if match([r"\b1pl\b", r"\bfirst party logistics\b", r"what is 1pl", r"explain 1pl", r"1pl meaning", r"define 1pl"]):
        return jsonify({"reply":
            "**1PL (First Party Logistics)** refers to the **owner of the goods** who handles all logistics themselves.\n\n"
            "This means the company manages:\n- Warehousing\n- Transportation\n- Inventory & dispatch\n\n"
            "**No outsourcing** is involved — everything is done in-house by the product owner."
        })

    # --- Transportation ---
    if match([
        r"\bfleet\b", r"dsv fleet", r"dsv transportation", r"truck fleet", r"transport fleet",
        r"fleet info", r"fleet of dsv", r"tell me about fleet", r"fleet trucks", r"dsv.*fleet", r"fleet.*dsv"
    ]):
        return jsonify({"reply":
            "DSV operates a large fleet in the UAE including:\n\n"
            "- 🚛 Flatbed trailers\n"
            "- 📦 Box trucks\n"
            "- 🚚 Double trailers\n"
            "- ❄️ Reefer trucks (chiller/freezer)\n"
            "- 🏗 Lowbeds\n"
            "- 🪨 Tippers\n"
            "- 🏙 Small city delivery trucks\n\n"
            "Fleet vehicles support all types of transport including full truckload (FTL), LTL, and container movements."
        })

    if match([
        r"transport.*t.?&.?c", r"transportation.*terms", r"transport.*conditions", r"transport.*policy",
        r"delivery terms", r"transport.*rules", r"transport.*regulations", r"transportation t and c",
        r"terms and conditions.*transport", r"terms.*logistics", r"truck.*t.?&.?c"
    ]):
        return jsonify({"reply":
            "**📦 Full Transportation Terms & Conditions:**\n\n"
            "🚛 **General Notes:**\n"
            "- Cargo height must not exceed truck limits (side sticks/headboard)\n"
            "- Permit-required locations (e.g., city limits) need 2–3 working days processing\n"
            "- Short-distance trips require loading and delivery on the same day\n\n"
            "📅 **Validity:**\n"
            "- Quotation valid for **15 days** from issuance\n\n"
            "💸 **Additional Fees:**\n"
            "- VAT: 5%\n"
            "- Environmental Fee: AED 10/trip/truck\n"
            "- From Jan 2025: 0.15% of invoice value\n\n"
            "📜 **Terms & Conditions:**\n"
            "- On FOT-to-FOT basis (Free On Truck at both ends)\n"
            "- Per trip per truck\n"
            "- General cargo only\n"
            "- Based on provided location — any changes require re-quote\n"
            "- Valid only for stable, flat, non-sandy areas\n"
            "- Subject to truck availability\n"
            "- Based on standard UAE truck specs\n"
            "- Loading/offloading under **customer scope**\n"
            "- Sharjah/Ajman require Municipality permissions\n"
            "- Detention: AED 150/hour after free period\n"
            "- Backhaul (same-day): +60% / next-day: full rate\n"
            "- Sundays/Holidays: trip rate +50%\n"
            "- Force majeure applies to delays from weather, traffic, etc.\n"
            "- Site details, maps, and contact must be provided 48 hours prior\n\n"
            "✅ **Inclusions:**\n"
            "- Fuel (Diesel)\n"
            "- DSV equipment & personnel insurance\n\n"
            "❌ **Exclusions (billed at actuals):**\n"
            "- Loading, offloading, supervision\n"
            "- Port charges, gate passes, tolls, permits\n"
            "- Cargo insurance, customs, VGM, washing\n\n"
            "❌ **Cancellation Charges:**\n"
            "- 50% if cancelled **before** truck placement\n"
            "- 100% if cancelled **after** truck placement\n"
            "- Waived if cancelled **24 hours** in advance\n\n"
            "Let me know if you'd like clarification on any specific point."
        })

    if match([r"truck types", r"trucks", r"transportation types", r"dsv trucks", r"transport.*available", r"types of transport", r"trucking services"]):
        return jsonify({"reply":
            "DSV provides local and GCC transportation using:\n"
            "- Flatbeds for general cargo\n"
            "- Lowbeds for heavy equipment\n"
            "- Tippers for construction bulk\n"
            "- Box trucks for secure goods\n"
            "- ❄️ Reefer trucks for temperature-sensitive cargo\n"
            "- Double trailers for long-haul\n"
            "- Vans and city trucks for last-mile delivery."
        })

    # === Individual Truck Types ===
    if match([
        r"reefer truck", r"reefer trucks", r"reefer truk", r"reefer trruck", r"reefer trrucks",
        r"chiller truck", r"chiller trucks", r"cold truck", r"cold trucks",
        r"refrigerated truck", r"refrigerated trucks"
    ]):
        return jsonify({"reply":
            "❄️ **Reefer Truck**: Temperature-controlled vehicle used to transport cold chain goods like food, pharmaceuticals, and chemicals.\n"
            "DSV reefer trucks operate between +2°C to –22°C and are equipped with GPS and real-time temperature tracking."
        })

    if match([r"flatbed", r"flatbed truck", r"what is flatbed", r"flatbed trailer"]):
        return jsonify({"reply":
            "🚛 **Flatbed Truck**: An open platform truck ideal for transporting heavy, oversized, or palletized cargo.\n"
            "Commonly used for containers, steel, and construction materials.\n"
            "Max capacity: ~22–25 tons."
        })

    if match([r"lowbed", r"low bed", r"lowbed trailer", r"what is lowbed"]):
        return jsonify({"reply":
            "🏗 **Lowbed Trailer**: Specialized truck used for transporting heavy equipment and oversized machinery.\n"
            "Has a lower deck height for tall cargo. Capacity up to 60 tons.\n"
            "Ideal for construction, infrastructure, and oil & gas projects."
        })

    if match([r"box truck", r"closed truck", r"curtainside truck", r"box type truck"]):
        return jsonify({"reply":
            "📦 **Box Truck**: Enclosed truck used for transporting general cargo protected from weather.\n"
            "Typically used for FMCG, electronics, retail, and secure goods.\n"
            "Capacity: ~5–10 tons."
        })

    if match([r"double trailer", r"articulated trailer", r"tandem trailer", r"double trailer truck"]):
        return jsonify({"reply":
            "🚚 **Double Trailer**: Articulated truck with two trailers, used for long-distance, high-volume transport.\n"
            "Can carry up to 50–60 tons total. Ideal for inter-emirate and GCC deliveries."
        })

    if match([r"tipper", r"tippers", r"tipper truck", r"dump truck"]):
        return jsonify({"reply":
            "🪨 **Tipper Truck**: Used for transporting and unloading bulk materials like sand, gravel, or soil.\n"
            "DSV tippers typically carry 15–20 tons and are commonly used in construction logistics."
        })

    if match([r"\btransportation\b", r"tell me about transportation", r"transport services", r"what is transportation", r"dsv transportation"]):
        return jsonify({"reply":
            "DSV offers full-service land transportation across the UAE and GCC. We operate a modern fleet including:\n"
            "- 🚛 Flatbeds (up to 25 tons)\n"
            "- 🏗 Lowbeds for heavy or oversized cargo\n"
            "- 🪨 Tippers for bulk material (sand, gravel, etc.)\n"
            "- 📦 Box trucks for protected cargo\n"
            "- ❄️ Reefer trucks (chiller/freezer) for temperature-controlled delivery\n"
            "- 🚚 Double trailers for high-volume long-haul moves\n"
            "- 🏙 Small city trucks for last-mile distribution\n\n"
            "All transport is coordinated by our OCC team in Abu Dhabi with real-time tracking, WMS integration, and documentation support."
        })

    if match([r"fot to fot", r"f\.o\.t to f\.o\.t", r"fot basis", r"what is fot", r"fot meaning", r"fot to fot basis"]):
        return jsonify({"reply":
            "**FOT to FOT basis** stands for *Free On Truck to Free On Truck*. It means:\n\n"
            "- 🚚 Cargo is picked up from the origin **on a truck**\n"
            "- 🚚 Delivered to the destination **on a truck**\n"
            "- ❌ Loading/unloading at either end is **not included**\n\n"
            "This term is commonly used in DSV transport quotes to define the scope of delivery responsibility."
        })

    if match([
        r"\bltl\b", r"less than truckload", r"ltl shipment", r"ltl meaning", r"what is ltl",
        r"\blcl\b", r"less than container load", r"lcl shipment", r"lcl meaning", r"what is lcl",
        r"\bftl\b", r"full truckload", r"what is ftl", r"ftl meaning", r"explain ftl"
    ]):
        return jsonify({"reply":
            "**Here’s a breakdown of common shipping terms:**\n\n"
            "🚛 **LTL (Less Than Truckload)**:\n"
            "- Road transport when cargo doesn’t fill a full truck\n"
            "- Shared with other shipments\n"
            "- Cost-effective for small or medium-sized loads\n\n"
            "🚢 **LCL (Less Than Container Load)**:\n"
            "- Sea freight where cargo doesn’t fill a container\n"
            "- Consolidated with other customers’ cargo\n"
            "- Ideal for partial-volume international shipments\n\n"
            "🚛 **FTL (Full Truckload)**:\n"
            "- Entire truck is booked for one customer\n"
            "- Faster and more secure\n"
            "- Best for high-volume, urgent, or dedicated deliveries\n\n"
            "DSV offers all three options depending on your cargo size, mode, and urgency."
        })

    # --- UAE Emirates Distance + Travel Time ---
    if match([r"abu dhabi.*dubai|dubai.*abu dhabi"]):
        return jsonify({"reply": "The distance between Abu Dhabi and Dubai is about **140 km**, and the travel time is approximately **2.5 hours**."})

    if match([r"abu dhabi.*sharjah|sharjah.*abu dhabi"]):
        return jsonify({"reply": "The distance between Abu Dhabi and Sharjah is about **160 km**, and the travel time is approximately **2.5 to 3 hours**."})

    if match([r"abu dhabi.*ajman|ajman.*abu dhabi"]):
        return jsonify({"reply": "The distance between Abu Dhabi and Ajman is approximately **170 km**, with a travel time of about **2.5 to 3 hours**."})

    if match([r"abu dhabi.*ras al khaimah|ras al khaimah.*abu dhabi|rak.*abu dhabi|abu dhabi.*rak"]):
        return jsonify({"reply": "The road distance from Abu Dhabi to Ras Al Khaimah is about **240 km**, and the travel time is around **3 to 3.5 hours**."})

    if match([r"abu dhabi.*fujairah|fujairah.*abu dhabi"]):
        return jsonify({"reply": "Abu Dhabi to Fujairah is approximately **250 km**, with a travel time of about **3 to 3.5 hours**."})

    if match([r"dubai.*sharjah|sharjah.*dubai"]):
        return jsonify({"reply": "Dubai to Sharjah is around **30 km**, and the travel time is typically **30 to 45 minutes**."})

    if match([r"dubai.*ajman|ajman.*dubai"]):
        return jsonify({"reply": "Dubai to Ajman is approximately **40 km**, and it takes around **60 to 90 minutes** by road."})

    if match([r"dubai.*ras al khaimah|ras al khaimah.*dubai|dubai.*rak|rak.*dubai"]):
        return jsonify({"reply": "The distance between Dubai and Ras Al Khaimah is around **120 km**, with a travel time of **2 to 2.5 hours**."})

    if match([r"dubai.*fujairah|fujairah.*dubai"]):
        return jsonify({"reply": "Dubai to Fujairah is approximately **130 km**, and the travel time is about **2.5 hours**."})

    if match([r"sharjah.*ajman|ajman.*sharjah"]):
        return jsonify({"reply": "Sharjah and Ajman are extremely close — only about **15 km**, with a travel time of **45 to 60 minutes**."})

    if match([r"sharjah.*fujairah|fujairah.*sharjah"]):
        return jsonify({"reply": "Sharjah to Fujairah is roughly **110 km**, and takes about **2 hours** by road."})

    if match([r"sharjah.*ras al khaimah|ras al khaimah.*sharjah|sharjah.*rak|rak.*sharjah"]):
        return jsonify({"reply": "Sharjah to Ras Al Khaimah is approximately **100 km**, and the travel time is around **2 to 2.5 hours**."})

    if match([
        r"truck capacity", r"how many ton", r"truck tonnage", r"truck.*can carry", r"truck load",
        r"flatbed.*ton", r"flatbed.*load", r"flatbed capacity",
        r"double trailer.*ton", r"articulated.*capacity",
        r"box truck.*ton", r"curtainside.*load", r"box truck capacity",
        r"reefer.*ton", r".*capacity", r"chiller truck.*load",
        r"city truck.*ton", r"1 ton truck", r"3 ton truck",
        r"lowbed.*ton", r"lowbed.*capacity",
        r"tipper.*ton", r"dump truck.*load", r"bulk truck.*ton"
    ]):
        return jsonify({"reply":
            "Here’s the typical tonnage each DSV truck type can carry:\n\n"
            "🚛 **Flatbed Truck**: up to 22–25 tons (ideal for general cargo, pallets, containers)\n"
            "🚚 **Double Trailer (Articulated)**: up to 50–60 tons combined (used for long-haul or inter-emirate)\n"
            "📦 **Box Truck / Curtainside**: ~5–10 tons (weather-protected for packaged goods)\n"
            "❄️ **Reefer Truck**: 3–12 tons depending on size (for temperature-sensitive cargo)\n"
            "🏙 **City Truck (1–3 Ton)**: 1 to 3 tons (last-mile delivery within cities)\n"
            "🏗 **Lowbed Trailer**: up to 60 tons (for heavy equipment and oversized machinery)\n"
            "🪨 **Tipper / Dump Truck**: ~15–20 tons (for bulk cargo like sand, gravel, or construction material)"
        })

    if match([r"(distance|how far|km).*mussafah.*(al markaz|markaz|hameem|hamim|ghayathi|ruwais|mirfa|madinat zayed|western region)"]):
        return jsonify({"reply":
            "Approximate road distances from Mussafah:\n"
            "- Al Markaz: **60 km**\n"
            "- Hameem: **90 km**\n"
            "- Madinat Zayed: **150 km**\n"
            "- Mirfa: **140 km**\n"
            "- Ghayathi: **240 km**\n"
            "- Ruwais: **250 km**\n"
            "\nLet me know if you need travel time or transport support too."
        })

    # --- Environmental Fee ---
    if match([r"environmental fee", r"environment fee", r"0\.15%.*fee", r"green surcharge", r"eco fee"]):
        return jsonify({"reply":
            "🚛 Environmental Fees:\n- AED 10.00 per trip/truck\n- Effective 1 Jan 2025: 0.15% of invoice value added as environmental surcharge."
        })

    # --- Cancellation Charges ---
    if match([r"cancellation charge", r"cancel.*trip", r"cancel.*transport", r"trip cancelled", r"transport cancellation policy"]):
        return jsonify({"reply":
            "**Cancellation Charges:**\n- ❌ 50% if cancelled before truck placement\n- ❌ 100% if cancelled after truck placement\n- ✅ No charge if cancelled 24 hours in advance."
        })

    # --- Validity ---
    if match([r"validity", r"quotation validity", r"how long.*quote", r"rate.*valid", r"validity of transport"]):
        return jsonify({"reply": "📅 Transport quotation validity is **15 days** from the date of issue."})

    # --- Loading / Offloading ---
    if match([r"loading.*included", r"offloading.*included", r"who loads", r"who unloads", r"customer.*loading", r"customer.*offloading"]):
        return jsonify({"reply": "🚫 Loading and offloading are under **customer scope**. DSV provides trucks on an FOT-to-FOT basis only."})

    # --- Backhaul / Backload ---
    if match([r"backhaul", r"backload", r"return trip", r"same day return", r"delivery back to origin"]):
        return jsonify({"reply":
            "🔄 **Backhaul/Backload Charges:**\n- Same-day return to origin: **+60%** of trip charge\n- Next-day return: **100%** of trip charge\n- Separate location = separate trip rate."
        })

    # --- Sharjah / Ajman Municipality ---
    if match([r"sharjah.*permission", r"ajman.*municipality", r"offload.*road", r"load.*outside", r"warehouse.*inside.*loading"]):
        return jsonify({"reply":
            "⚠️ For Sharjah & Ajman:\n- Customer must arrange **Municipality loading/offloading permission**\n- Operations must happen **inside premises only** — activity on the road is not allowed and fines will be passed to the client."
        })

    # --- Inclusions / Exclusions ---
    if match([r"what.*included", r"included.*transport", r"transport.*inclusions"]):
        return jsonify({"reply": "✅ **Inclusions:**\n- DSV Equipment Insurance\n- Personnel Insurance\n- Fuel (Diesel)"})

    if match([r"what.*excluded", r"excluded.*transport", r"transport.*exclusions"]):
        return jsonify({"reply": "❌ **Exclusions:**\n- Loading/Offloading/Supervision\n- Port handling, customs, tolls, permits, road taxes, gate passes, washing, cargo insurance, and third-party fees."})

    # --- Force Majeure ---
    if match([r"force majeure", r"weather condition", r"sandstorm", r"rain.*delay", r"high wind", r"delays due to weather"]):
        return jsonify({"reply":
            "🌪️ **Force Majeure Clause:**\nDelays due to weather (sandstorms, rain, wind) or unforeseen events are considered normal working hours. Detention will apply beyond free hours. DSV reserves the right to claim costs if delays impact delivery."
        })

    # --- Detention Charges ---
    if match([r"detention", r"detention charges", r"wait time charges", r"extra time", r"delays at site", r"truck waiting"]):
        return jsonify({"reply": "🕒 **Detention Charges:**\n- AED 150 per truck after 1 free hour of waiting at site."})

    # --- DSV Abu Dhabi Facility Sizes ---
    if match([
        r"plot size", r"abu dhabi total area", r"site size", r"facility size", r"total sqm", r"how big",
        r"yard size", r"open yard area", r"size of open yard", r"open yard.*size", r"area of open yard"
    ]):
        return jsonify({"reply": "DSV Abu Dhabi's open yard spans 360,000 SQM across Mussafah and KIZAD. The total logistics plot is 481,000 SQM, including ~100,000 SQM of service roads and utilities, and a 21,000 SQM warehouse (21K)."})

    if match([r"sub warehouse|m44|m45|al markaz|abu dhabi warehouse total|all warehouses"]):
        return jsonify({"reply": "In addition to the main 21K warehouse, DSV operates sub-warehouses in Abu Dhabi: M44 (5,760 sqm), M45 (5,000 sqm), and Al Markaz (12,000 sqm). Combined with 21K, the total covered warehouse area in Abu Dhabi is approximately 44,000 sqm."})

    if match([r"terms and conditions|quotation policy|T&C|billing cycle|operation timing|payment terms|quotation validity"]):
        return jsonify({"reply": "DSV quotations include the following terms: Monthly billing, final settlement before vacating, 15-day quotation validity, subject to space availability. The depot operates Monday–Friday 8:30 AM to 5:30 PM. Insurance is not included by default. An environmental fee of 0.15% is added to all invoices. Non-moving cargo over 3 months may incur extra storage tariff."})

    if match([r"safety training|warehouse training|fire drill|manual handling|staff safety|employee training|toolbox talk"]):
        return jsonify({"reply": "DSV staff undergo regular training in fire safety, first aid, manual handling, emergency response, and site induction. We also conduct toolbox talks and refresher sessions to maintain safety awareness and operational excellence."})

    if match([r"adnoc|adnoc project|dsv.*adnoc|oil and gas project|dsv support.*adnoc|logistics for adnoc"]):
        return jsonify({"reply": "DSV has an active relationship with ADNOC and its group companies, supporting logistics for Oil & Gas projects across Abu Dhabi. This includes warehousing of chemicals, fleet transport to remote sites, 3PL for EPC contractors, and marine logistics for ADNOC ISLP and offshore projects. All operations are QHSE compliant and meet ADNOC’s safety and performance standards."})

# FM-200 quick explainer
    if match([r"\bfm\s*-?\s*200\b", r"\bfm200\b"]):
        return jsonify({"reply":
            "🔒 **FM‑200 (HFC‑227ea)** is a clean‑agent fire suppression system used in sensitive areas (like RMS). "
            "It extinguishes fires quickly by absorbing heat, leaves no residue, and is safe for documents and electronics when applied per design."})
        
    if match([r"summer break|midday break|working hours summer|12.*3.*break|uae heat ban|no work afternoon|hot season schedule"]):
        return jsonify({"reply": "DSV complies with UAE summer working hour restrictions. From June 15 to September 15, all outdoor work (including open yard and transport loading) is paused daily between 12:30 PM and 3:30 PM. This ensures staff safety and follows MOHRE guidelines."})

    if match([
        r"like what", r"such as", r"for example", r"what kind of help",
        r"what.*can.*you.*help.*with",
        r"what.*do.*you.*do",
        r"what.*things.*you.*can.*do",
        r"can.*you.*give.*example",
        r"what.*services.*you.*offer",
        r"what.*can.*u.*do",
        r"what.*can.*u.*help",
        r"what.*you.*provide",
        r"^what\s*services\??$",
        r"^services\??$",
        r"\bwhat\s+services\b",
        r"\bwhat\s*service\??$",
        
    ]):
        return jsonify({"reply":
            "Sure! I can help you with:\n\n"
            "📦 Storage rates (Standard, Chemical, Open Yard)\n"
            "🚛 Transport & truck types (flatbeds, reefers, lowbeds...)\n"
            "🧾 Value Added Services like packing, labeling, inventory\n"
            "🏢 DSV warehouse layouts, temperature zones, and chambers\n"
            "📍 UAE-wide transport routes & distances\n"
            "📚 Relocation, asset management, and more\n\n"
            "Ask me about anything related to DSV warehousing, logistics, or transport!"
        })

    if match([
        r"who are you", r"who r u", r"who.*you", r"who.*are.*you", r"what.*can.*you.*do",
        r"what can u do", r"what can you help with", r"how can you help", r"can u help", r"what can u help me with",
        r"how u help", r"your purpose", r"your role", r"what do u do", r"what.*can.*you.*answer",
        r"what.*assist.*me.*with", r"what.*can.*u.*assist", r"how.*can.*u.*support", r"what.*you.*do", r"how.*u.*can.*help"
    ]):
        return jsonify({"reply":
            "I'm the DSV logistics assistant 🤖 here to help you with:\n\n"
            "- 📦 Storage rates (Standard, Chemical, Open Yard)\n"
            "- 🚛 Transportation options and truck types\n"
            "- 🧾 Value Added Services (VAS)\n"
            "- 🏢 Warehouse info: size, layout, chambers\n"
            "- 🧊 Temperature zones, RMS, training\n"
            "- 📍 Distances and service locations across the UAE\n\n"
            "Ask me anything related to DSV warehousing, transport, or logistics!"
        })

    if match([r"how many.*facility", r"how many.*facilities", r"dsv abu dhabi facilities", r"how many warehouse.*dsv"]):
        return jsonify({"reply":
            "DSV Abu Dhabi operates multiple logistics facilities:\n\n"
            "- 🏢 **21K Warehouse (Mussafah)** – 21,000 sqm\n"
            "- 🏢 **M44** – 5,760 sqm\n"
            "- 🏢 **M45** – 5,000 sqm\n"
            "- 🏢 **Al Markaz (Hameem)** – 12,000 sqm\n"
            "- 🏗 **Open Yard (Mussafah + KIZAD)** – 360,000 sqm\n\n"
            "In total: **~44,000 sqm** of covered warehouse and **481,000 sqm** logistics site including service roads."
        })

    # --- DSV Abu Dhabi Short Location ---
    if match([
        r"dsv location", r"dsv abu dhabi location", r"where is dsv", r"dsv address", r"main office location",
        r"where is dsv abu dhabi", r"location of dsv", r"where.*dsv.*located", r"head office address"
    ]):
        return jsonify({"reply":
            "📍 DSV Abu Dhabi Location:\n"
            "M-19, Mussafah Industrial Area, Abu Dhabi, UAE\n"
            "📞 +971 2 555 2900\n"
            "🗺️ <a href=\"https://goo.gl/maps/tnFcmydbvdJ9gGLy8\" target=\"_blank\" rel=\"noopener noreferrer\">Open on Google Maps</a>"        
        })

    # --- Friendly Chat ---
    if match([r"\bhello\b|\bhi\b|\bhey\b|good morning|good evening"]):
        return jsonify({"reply": "Hello! I'm here to help with anything related to DSV logistics, transport, or warehousing."})

    if match([r"how.?are.?you|how.?s.?it.?going|whats.?up"]):
        return jsonify({"reply": "I'm doing great! How can I assist you with DSV services today?"})

    if match([r"\bthank(s| you)?\b|thx|appreciate"]):
        return jsonify({"reply": "You're very welcome! 😊"})

    # --- Fallback ---
    return jsonify({"reply": "I didn’t catch that—could you share a bit more detail about your DSV storage, transport, or VAS question?"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
