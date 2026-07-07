import streamlit as st
import pdfplumber
import pytesseract
from PIL import Image
from datetime import datetime
from llm import autofill_from_text

st.set_page_config(page_title="AI Form Autofill", layout="centered")
st.title("Dynamic Form Builder + AI Autofill")

# ---------------- SESSION STATE ----------------
if "schema" not in st.session_state:
    st.session_state.schema = []   # list of {label, type, required, options}
if "extracted_text" not in st.session_state:
    st.session_state.extracted_text = ""
if "form_data" not in st.session_state:
    st.session_state.form_data = {}
if "pending_autofill" not in st.session_state:
    st.session_state.pending_autofill = None   # holds AI result until it's safe to apply

FIELD_TYPES = ["Text", "Multiline", "Number", "Date", "Dropdown", "Checkbox"]


# ================= APPLY PENDING AUTOFILL (MUST RUN BEFORE ANY PREVIEW WIDGET IS CREATED) =================
def apply_pending_autofill():
    """
    Writes AI-extracted values into the widget session_state keys.
    This MUST run before st.text_input/selectbox/etc. for the same keys
    are instantiated in this script run, otherwise Streamlit raises:
    'cannot be modified after widget with key X instantiated'.
    """
    result = st.session_state.pending_autofill
    if result is None:
        return

    for field in st.session_state.schema:
        label = field["label"]
        widget_key = f"preview_{label}"

        if label not in result or result[label] is None:
            continue  # missing value -> leave blank / unchanged

        value = result[label]

        try:
            if field["type"] == "Number":
                value = float(value)
                st.session_state[widget_key] = value

            elif field["type"] == "Checkbox":
                if isinstance(value, str):
                    value = value.strip().lower() in ("true", "yes", "1")
                st.session_state[widget_key] = bool(value)

            elif field["type"] == "Dropdown":
                opts = field["options"] or []
                if value in opts:
                    st.session_state[widget_key] = value
                # if AI value isn't a valid option, skip silently (never hallucinate)

            elif field["type"] == "Date":
                # best-effort parse; skip if format is unrecognized
                parsed = None
                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
                    try:
                        parsed = datetime.strptime(str(value), fmt).date()
                        break
                    except ValueError:
                        continue
                if parsed:
                    st.session_state[widget_key] = parsed

            else:  # Text, Multiline
                st.session_state[widget_key] = str(value)

            # keep the plain data dict in sync too
            st.session_state.form_data[label] = st.session_state[widget_key]

        except (ValueError, TypeError):
            continue  # couldn't safely convert -> leave field as-is

    st.session_state.pending_autofill = None  # consumed


apply_pending_autofill()

# ================= TASK 1: FORM BUILDER =================
st.header("1. Dynamic Form Builder")

with st.form("add_field_form", clear_on_submit=True):
    col1, col2, col3 = st.columns([3, 2, 1])
    label = col1.text_input("Field Label")
    ftype = col2.selectbox("Field Type", FIELD_TYPES)
    required = col3.checkbox("Required")
    options_str = ""
    if ftype == "Dropdown":
        options_str = st.text_input("Dropdown options (comma separated)")
    submitted = st.form_submit_button("Add Field")
    if submitted:
        if label.strip() == "":
            st.error("Label cannot be empty.")
        else:
            st.session_state.schema.append({
                "label": label,
                "type": ftype,
                "required": required,
                "options": [o.strip() for o in options_str.split(",") if o.strip()] if ftype == "Dropdown" else []
            })
            st.rerun()

# List existing fields with delete buttons
for i, field in enumerate(st.session_state.schema):
    c1, c2 = st.columns([5, 1])
    c1.write(f"**{field['label']}** ({field['type']}){' *required*' if field['required'] else ''}")
    if c2.button("Delete", key=f"del_{i}"):
        st.session_state.schema.pop(i)
        st.rerun()

# ================= LIVE PREVIEW =================
st.header("Live Preview")

if not st.session_state.schema:
    st.info("No fields yet. Add a field above.")
else:
    for field in st.session_state.schema:
        label = field["label"]
        widget_key = f"preview_{label}"
        current_val = st.session_state.get(widget_key)
        is_missing = field["required"] and (current_val is None or current_val == "")
        display_label = f"⚠️ {label} (required)" if is_missing else label

        if field["type"] == "Text":
            if widget_key not in st.session_state:
                st.session_state[widget_key] = ""
            st.session_state.form_data[label] = st.text_input(display_label, key=widget_key)

        elif field["type"] == "Multiline":
            if widget_key not in st.session_state:
                st.session_state[widget_key] = ""
            st.session_state.form_data[label] = st.text_area(display_label, key=widget_key)

        elif field["type"] == "Number":
            if widget_key not in st.session_state:
                st.session_state[widget_key] = 0.0
            st.session_state.form_data[label] = st.number_input(display_label, key=widget_key)

        elif field["type"] == "Date":
            if widget_key not in st.session_state:
                st.session_state[widget_key] = datetime.today().date()
            st.session_state.form_data[label] = str(st.date_input(display_label, key=widget_key))

        elif field["type"] == "Dropdown":
            opts = field["options"] or ["(no options set)"]
            if widget_key not in st.session_state or st.session_state[widget_key] not in opts:
                st.session_state[widget_key] = opts[0]
            st.session_state.form_data[label] = st.selectbox(display_label, opts, key=widget_key)

        elif field["type"] == "Checkbox":
            if widget_key not in st.session_state:
                st.session_state[widget_key] = False
            st.session_state.form_data[label] = st.checkbox(display_label, key=widget_key)

# ================= TASK 2: UPLOAD =================
st.header("2. Upload Document")

uploaded_file = st.file_uploader("Upload PDF, PNG, JPG, JPEG", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file is not None:
    if not st.session_state.schema:
        st.error("Please create at least one field first.")
    else:
        st.success(f"File '{uploaded_file.name}' uploaded successfully.")

# ================= TASK 3: EXTRACT TEXT =================
st.header("3. Extract Document Text")

def extract_text(file):
    ext = file.name.split(".")[-1].lower()
    try:
        if ext == "pdf":
            text = ""
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            if text.strip():
                return text
            # fallback to OCR on PDF page images
            text = ""
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    im = page.to_image(resolution=200).original
                    text += pytesseract.image_to_string(im) + "\n"
            return text
        elif ext in ["png", "jpg", "jpeg"]:
            img = Image.open(file)
            return pytesseract.image_to_string(img)
        else:
            return None
    except Exception as e:
        st.error(f"Corrupted or unreadable file: {e}")
        return None

if st.button("Extract Text"):
    if not st.session_state.schema:
        st.error("Please create at least one field first.")
    elif uploaded_file is None:
        st.error("Please upload a document first.")
    else:
        result = extract_text(uploaded_file)
        if result is None:
            st.error("Unsupported or corrupted file.")
        elif result.strip() == "":
            st.error("No text could be extracted from this file.")
        else:
            st.session_state.extracted_text = result
            st.success("Text extracted successfully.")

if st.session_state.extracted_text:
    with st.expander("View Extracted Text"):
        st.text_area("Extracted Text", st.session_state.extracted_text, height=200)

# ================= TASK 4 & 5: AI AUTOFILL =================
st.header("4. AI Autofill")

if st.button("Autofill Form with AI"):
    if not st.session_state.schema:
        st.error("Please create a form first.")
    elif not st.session_state.extracted_text:
        st.error("Please extract text first.")
    else:
        with st.spinner("Calling Gemini..."):
            try:
                result = autofill_from_text(
                    st.session_state.extracted_text,
                    st.session_state.schema
                )
                # Do NOT write to widget session_state here — widgets for this
                # run were already instantiated above (Live Preview section
                # runs earlier in the script). Defer the write to next run.
                st.session_state.pending_autofill = result
                st.success("Autofill complete! Applying to form...")
                st.rerun()
            except Exception as e:
                st.error(str(e))

# ================= TASK 6: REVIEW & SAVE =================
st.header("5. Review & Save")

if st.button("Save"):
    missing_required = [
        f["label"] for f in st.session_state.schema
        if f["required"] and not st.session_state.form_data.get(f["label"])
    ]
    if missing_required:
        st.error(f"Please fill required fields: {', '.join(missing_required)}")
    else:
        st.success("Saved successfully!")
        st.json(st.session_state.form_data)