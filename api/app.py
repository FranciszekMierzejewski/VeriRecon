"""
FastAPI backend for VeriRecon.
Accepts an uploaded invoice file, runs it through Gemini extraction and the ADK reconciliation agent, and returns classification.
Acts as trigger endpoint instead of seperate manual extraction and agent scripts.
"""

import logging
import shutil # shell utilities for file/directory operations
import tempfile 

from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile

from agent.main_agent import reconcile_invoice
from extraction.gemini_extract import extract_invoice_from_file
from api.schemas import ReconciliationResponse



logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title = "Vericon",
    description = "Autonomous invoice reconciliation agent"
)

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

@app.get("/health")
def health():
    return {"status":"ok"}

@app.post("/reconcile", response_model = ReconciliationResponse)
async def reconcile_invoice_upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422, # successfully parsed request, but semantic or data validation errors
            detail = f"Unsupported file type '{suffix}'. Allowed {sorted(ALLOWED_EXTENSIONS)}"
        )

    # Write the upload to a temp file, since extract_invoice_from_file
    # expects a filesystem path rather than an in-memory stream
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
        tmp_path = Path(temp.name)
        shutil.copyfileobj(file.file, temp)

    try:
        try:
            extracted_invoice = extract_invoice_from_file(tmp_path)
        except FileNotFoundError as exception:
            logger.exception("Temp file missing during extraction")
            raise HTTPException(status_code=500, detail=str(exception))
        except ValueError as exception:
            # Raised by extract_invoice_from_file for empty/blocked Gemini response
            logger.exception("Extraction failed")
            raise HTTPException(status_code=422, detail=f"Extraction failed: {str(exception)}")

        required_fields = {"invoice_number", "supplier", "invoice_amount", "currency", "date"}
        missing = required_fields - extracted_invoice.keys()
        if missing:
            logger.warning("Extraction missing required fields: %s", missing)
            raise HTTPException(
                status_code=422,
                detail=f"Extraction did not return required fields: {sorted(missing)}",
            )

        try:
            classification = reconcile_invoice(extracted_invoice)
        except Exception:
            # Agent/Firestore/Gemini failures during reconciliation 
            logger.exception("Reconciliation failed after successful extraction")
            raise HTTPException(status_code=500, detail="Reconciliation failed")

        return ReconciliationResponse(
            invoice=extracted_invoice,
            classification=classification,
        )

    except HTTPException:  # keep original error, not overwritten
        raise
    except Exception:
        logger.exception("Unexpected error during invoice reconciliation")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        tmp_path.unlink(missing_ok=True) # deletes file at path with no error if not exist