import os
import json
import re
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Use a currently supported model
model = genai.GenerativeModel("gemini-2.5-flash")


def autofill_from_text(document_text, schema):
    schema_text = "\n".join(
        [
            f"- {field['label']} ({field['type']})"
            for field in schema
        ]
    )

    prompt = f"""
You are an information extraction engine.

Extract ONLY the fields listed below.

If a value cannot be found,
return null.

Do NOT guess.

Return ONLY JSON.

Fields:

{schema_text}

Document:

{document_text}
"""

    response = model.generate_content(prompt)

    raw = response.text.strip()

    print("\n================ RAW RESPONSE ================\n")
    print(raw)
    print("\n==============================================\n")

    # Remove markdown code fences if Gemini adds them
    raw = raw.replace("```json", "")
    raw = raw.replace("```", "").strip()

    # Extract JSON block even if Gemini adds text
    match = re.search(r"\{[\s\S]*\}", raw)

    if not match:
        raise Exception("Gemini did not return valid JSON.")

    json_string = match.group()

    result = json.loads(json_string)

    print("Parsed JSON:")
    print(result)

    return result