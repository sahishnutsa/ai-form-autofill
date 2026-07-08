"""
Gemini-based form autofill.

This module extracts structured field values from document text
using the Gemini API and returns the results as a Python dictionary.
"""

import json
import os
import re

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Use a currently supported Gemini model.
model = genai.GenerativeModel("gemini-2.5-flash")


def autofill_from_text(document_text, schema):
    """
    Extract form field values from document text using Gemini.
    """

    schema_text = "\n".join(
        f"- {field['label']} ({field['type']})"
        for field in schema
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
    raw_response = response.text.strip()

    print("\n================ RAW RESPONSE ================\n")
    print(raw_response)
    print("\n==============================================\n")

    # Remove Markdown code fences if present.
    raw_response = (
        raw_response
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )

    # Extract the JSON object even if extra text is returned.
    match = re.search(r"\{[\s\S]*\}", raw_response)

    if not match:
        raise Exception("Gemini did not return valid JSON.")

    json_string = match.group()
    parsed_data = json.loads(json_string)

    print("Parsed JSON:")
    print(parsed_data)

    return parsed_data