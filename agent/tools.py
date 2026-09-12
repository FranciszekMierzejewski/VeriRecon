"""
Tools for agent to use during invoice reconciliation.
ADK agent calls each function independently, with their results they are logged as an audit trail when flagged.
"""

from datetime import datetime
from typing import Any

from google.cloud.firestore_v1.base_query import FieldFilter

from db.firestore_client import get_client, sanitise_document_id

database = get_client()

def lookup_po(po_reference: str) -> dict[str, Any]:
    """
    Look up a purchase order by reference in Firestore.
    Bool flag on 'found', set to false if no matching document exists, for invoices with missing/manipulated PO reference.
    """

    if not po_reference:
        return {"found" : False, "po_reference" : po_reference}

    document_reference = database.collection("purchase_orders").document(po_reference)
    document = document_reference.get() # DocumentSnapshot object wraps metadata (exists, id, ref) and document data

    if not document.exists:
        return {"found": False, "po_reference": po_reference}

    po_data = document.to_dict() # grab doc data

    if po_data is None: # Firestore cannot guarantee non-empty fields
        return {"found" : False, "po_reference" : po_reference}
    
    return {
        "found" : True,
        "po_reference" : po_reference,
        "supplier" : po_data.get("supplier"),
        "po_amount" : po_data.get("total"),
        "status" : po_data.get("status"),
        "line_content" : po_data.get("line_content", []),
        "created_date" : po_data.get("created_date")
    }


def get_vendor_history(supplier: str) -> dict[str, Any]:
    """
    Retrieve historical statistics for a supplier from Firestore. 
    For instance, a new supplier has 'found' set to false as no prior record
    """ 

    document_id = sanitise_document_id(supplier)
    document_reference = database.collection("vendor_history").document(document_id)
    document = document_reference.get()

    if not document.exists:
        return {"found" : False, "supplier" : supplier}

    history_data = document.to_dict()

    if history_data is None:
        return {"found" : False, "supplier" : supplier}

    return {
        "found": True,
        "supplier": supplier,
        "average_invoice_amount": history_data.get("average_invoice_amount"),
        "invoice_count": history_data.get("invoice_count"),
        "past_flags_count": history_data.get("past_flags_count"),
        "ytd_spend": history_data.get("ytd_spend"),
        "payment_terms": history_data.get("payment_terms")
    }


def compute_variance(invoice_amount: float, po_amount: float, tolerance_gbp: float = 5.0) -> dict[str, Any]:
    """
    Calculate the difference between an invoice amount and its matched PO amount. 
    Positive variance is interpreted as overbilling.
    """

    if po_amount == 0:
        return {
            "variance_pct" : None,
            "direction" : "undefined",
            "within_tolerance" : False,
            "error" : "po_amount is zero, cannot compute variance"
        }


    # over £5 is flagged, else acceptable diff
    absolute_diff = round(invoice_amount - po_amount, 2)
    direction = "over" if absolute_diff > 0 else "under" if absolute_diff < 0 else "exact"
    within_tolerance = abs(absolute_diff) <= tolerance_gbp

    return {
        "abs_difference" : absolute_diff,
        "direction" : direction,
        "within_tolerance" : within_tolerance
    }


def verify_invoice_arithmetic(
    invoice_amount: float,
    subtotal: float | None = None,
    tax_amount: float | None = None,
    shipping_cost: float | None = None,
    other_charges: float | None = None,
    tolerance_gbp: float = 5.0,
) -> dict[str, Any]:
    """
    Check whether an invoice's own stated breakdown (subtotal + tax +
    shipping + other charges) actually sums to its stated invoice_amount.

    This is independent of PO matching, it catches internal arithmetic
    inconsistency on the invoice itself (e.g. inflated VAT or padded
    shipping that wouldn't show up as a line-item price change), which
    compute_variance cannot detect since it only compares against the PO.

    All breakdown fields are optional since not every invoice itemizes
    them separately. Missing fields are treated as 0 in the sum, and
    fields_provided reports which ones were actually available, so the
    agent can weigh a full breakdown differently from a partial one.
    """

    fields = {
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "shipping_cost": shipping_cost,
        "other_charges": other_charges,
    }
    fields_provided = [name for name, value in fields.items() if value is not None]

    if not fields_provided:
        return {
            "checked": False,
            "reason": "No breakdown fields (subtotal, tax_amount, shipping_cost, other_charges) were provided",
        }

    computed_total = sum(value for value in fields.values() if value is not None)
    absolute_diff = round(invoice_amount - computed_total, 2)
    is_consistent = abs(absolute_diff) <= tolerance_gbp

    return {
        "checked": True,
        "fields_provided": fields_provided,
        "computed_total": round(computed_total, 2),
        "invoice_amount": invoice_amount,
        "abs_difference": absolute_diff,
        "is_consistent": is_consistent,
    }


def _parse_invoice_date(date_str: str) -> datetime | None:
    """
    Previously only candidate dates were guarded with try/except, so the
    input invoice_date itself had no protection, so a malformed date
    from extraction (e.g. wrong format slipping through) would crash
    the whole tool call instead of degrading gracefully.
    """
    try:
        return datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        return None


def check_exact_duplicate(supplier: str, invoice_amount: float, invoice_date: str, exclude_invoice_number: str | None = None) -> dict[str, Any]:
    """
    Check Firestore for a prior invoice with matching supplier, amount, and date for an exact match, hard block.
    """

    query = (
        database.collection("processed_invoices")
        .where(filter=FieldFilter("supplier", "==", supplier))
        .where(filter=FieldFilter("invoice_amount", "==", invoice_amount))
        .where(filter=FieldFilter("date", "==", invoice_date))
    )

    matches = list(query.stream())

    # In the case where invoice currently being checked was already written to avoid matching itself
    if exclude_invoice_number is not None:
        matches = [m for m in matches if m.id != exclude_invoice_number]
    
    if not matches:
        return {"is_duplicate" : False}

    return {
        "is_duplicate" : True,
        "matching_invoice_ids" : [m.id for m in matches]
    }


def check_fuzzy_duplicates(
    supplier: str, 
    invoice_amount: float, 
    invoice_date: str, 
    amount_tolerance_gbp: float = 5.0, 
    date_window_days: int = 5,
    exclude_invoice_number: str | None = None
    ) -> dict[str, Any]:
    """
    Check for near-duplicates that check_exact_duplicate would miss
    """

    invoice_dt = _parse_invoice_date(invoice_date)

    if invoice_dt is None:
        return {
            "is_fuzzy_duplicate": False,
            "error": f"could not parse invoice_date '{invoice_date}' as DD/MM/YYYY"
        }

    query = database.collection("processed_invoices").where(
        filter=FieldFilter("supplier", "==", supplier)
    )
    candidates = list(query.stream())

    fuzzy_matches: list[dict[str, Any]] = []

    for candidate in candidates:
        if exclude_invoice_number is not None and candidate.id == exclude_invoice_number:
            continue

        data = candidate.to_dict()

        if data is None:
            continue

        candidate_amount = data.get("invoice_amount")
        candidate_date_str = data.get("date")

        if candidate_amount is None or candidate_date_str is None:
            continue

        amount_diff = abs(invoice_amount - candidate_amount)
        if amount_diff > amount_tolerance_gbp:
            continue

        candidate_dt = _parse_invoice_date(candidate_date_str)
        if candidate_dt is None:
            continue

        date_diff_days = abs((invoice_dt - candidate_dt).days)
        if date_diff_days > date_window_days:
            continue

        fuzzy_matches.append({
            "invoice_id": candidate.id,
            "amount_diff_gbp": round(amount_diff, 2),
            "date_diff_days": date_diff_days
        })

    if not fuzzy_matches:
        return {"is_fuzzy_duplicate": False}

    return {
        "is_fuzzy_duplicate": True,
        "matches": fuzzy_matches
    }