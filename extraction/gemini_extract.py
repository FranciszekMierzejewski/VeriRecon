"""
Multimodal invoice extraction via Gemini (Vertex AI)

Takes a PDF or image file and returns structured invoice data matching the schema produced by data/generate_data.py, 
so extraction output can be reconciled directly against Firestore Purchase Order records with no translation layer required.
"""

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai.types import HttpOptions, Part

load_dotenv()

PROJECT_ID: str = os.environ.get("GCP_PROJECT_ID", "verirecon-hackathon")
LOCATION: str = os.environ.get("GCP_LOCATION", "global")
MODEL_ID: str = "gemini-3.5-flash"

_client: genai.Client | None = None


def get_genai_client() -> genai.Client:
    """Reuses a single genai client instance across calls."""
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=LOCATION,
            http_options=HttpOptions(api_version="v1")
        )
    return _client


# Structured output schema - matches field names used in data/generate_data.py
# (supplier, po_reference, invoice_amount, currency, date) so downstream
# reconciliation code doesn't need a field-mapping step.

# nullable instead of type union
INVOICE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": "string"},
        "supplier": {"type": "string"},
        "po_reference": {
            "type": "string",
            "nullable": True,
            "description": "Purchase order number referenced on the invoice, if present"
        },
        "subtotal": {
            "type": "number",
            "nullable": True,
            "description": "Sum of line items before tax and additional charges, if stated on the invoice"
        },
        "shipping_cost": {
            "type": "number",
            "nullable": True,
            "description": "Shipping or delivery charge, if stated separately on the invoice"
        },
        "other_charges": {
            "type": "number",
            "nullable": True,
            "description": "Any other additional charges stated separately, if present"
        },
        "tax_amount": {
            "type": "number",
            "nullable": True,
            "description": "VAT/tax amount, if stated separately on the invoice"
        },
        "invoice_amount": {
            "type": "number",
            "description": "Total amount due, including tax and charges"
        },
        "currency": {"type": "string"},
        "date": {
            "type": "string",
            "description": "Invoice date in DD/MM/YYYY format"
        },
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "line_total": {"type": "number"}
                },
                "required": ["description", "quantity", "unit_price", "line_total"],
            },
        },
    },
    "required": ["invoice_number", "supplier", "invoice_amount", "currency", "date"],
}

EXTRACTION_PROMPT = """
You are extracting structured data from a UK supplier invoice for a
truck/HGV parts business.

Extract only the values found in the document. Do not infer, guess, or
normalise values beyond what is explicitly stated. If a field (e.g.
po_reference) is not present on the invoice, return null for it rather
than guessing.

Critical: extract tax_amount, subtotal, shipping_cost, and other_charges
exactly as printed on the invoice, even if they appear mathematically
inconsistent with the invoice's stated total or with each other. Do not
recalculate, correct, or infer any of these values — for example, if
VAT is labeled "20%" but the printed amount does not equal 20% of the
subtotal, extract the printed amount as-is. Your job is transcription,
not verification; arithmetic consistency is checked separately downstream.

Return the invoice date in DD/MM/YYYY format.
""".strip()


def extract_invoice_from_file(file_path: str | Path) -> dict[str, Any]:
    """
    Extract structured invoice data from a local PDF or image file.

    Raises FileNotFoundError if file_path doesn't exist, and
    json.JSONDecodeError if the model response isn't valid JSON
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Invoice file not found: {file_path}")

    mime_type = _infer_mime_type(file_path)
    client = get_genai_client()

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    document_part = Part.from_bytes(data=file_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model=MODEL_ID,
        contents=[document_part, EXTRACTION_PROMPT],
        config={
            "response_mime_type": "application/json",
            "response_schema": INVOICE_RESPONSE_SCHEMA
        },
    )

    if response.text is None:
        raise ValueError("Gemini returned no text content, response may have blocked or empty. Check response.candidates for further details")
    
    return json.loads(response.text)


def _infer_mime_type(file_path: Path) -> str:
    """Map file extension to MIME type for supported invoice formats."""
    suffix = file_path.suffix.lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp"
    }
    if suffix not in mime_map:
        raise ValueError(
            f"Unsupported file type: {suffix}. Supported: {list(mime_map.keys())}"
        )
    return mime_map[suffix]


if __name__ == "__main__":
    # Quick manual test against a sample file in data/sample_pdfs/

    sample_directory = Path("data/sample_pdfs")
    pdf_files = list(sample_directory.iterdir())

    if not pdf_files:
        print(f"No sample file found at {sample_directory}, add one to test extraction.")
    else:
        for sample_path in pdf_files:
            print(sample_path.name)
            result = extract_invoice_from_file(sample_path)
            print(json.dumps(result, indent = 2), "\n")