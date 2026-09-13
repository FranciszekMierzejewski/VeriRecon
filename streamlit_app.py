"""
Upload an invoice file to FastAPI backend and see its classification.
Review queue of previously processed invoices from Firestore, and review its reasoning chain audit trail.
"""

import os

import requests
import streamlit as st
from dotenv import load_dotenv

from db.firestore_client import get_client

load_dotenv()

API_URL = os.environ.get("VERIRECON_API_URL") or st.secrets.get("VERIRECON_API_URL", "http://127.0.0.1:8000")
database = get_client()

st.set_page_config(
    page_title = "VeriRecon"
)

st.title("VeriRecon - Invoice Reconciliation Agent")

with st.expander("What do the three outcomes mean?"):
    st.write("**Auto Approve** - PO matched, amount within tolerance, no duplicates. No human review needed.")
    st.write("**Flag For Review** - PO matched but the amount is outside tolerance, or the invoice's own numbers don't add up. A human should double-check.")
    st.write("**Escalate** - no matching PO found, or a duplicate/near-duplicate was detected. Treat with more scrutiny.")

def render_decision_banner(decision: str) -> None:
    """
    Different colours rendered per decision.
    """
    if decision == "Auto Approve":
        st.success(f"Decision: {decision}") # match within error bounds
    elif decision == "Flag For Review":
        st.warning(f"Decision: {decision}") # human review, minor issue
    elif decision == "Escalate":
        st.error(f"Decision: {decision}") # human review, major issue
    else:
        st.info(f"Decision: {decision}") # _parse_classification always escalates, but if some edge case occurs, fall back on this


def render_classification(result: dict) -> None:
    invoice = result["invoice"]
    classification = result["classification"]
    decision = classification.get("decision", "Unknown")

    render_decision_banner(decision)
    st.write(f"**Confidence:** {classification.get('confidence', 'N/A')}") # bold md, grab value of dict pair or default to N/A
    st.write(f"**Reasoning:** {classification.get('reasoning', 'N/A')}")

    flags = classification.get("flags", [])
    if flags:
        st.write("**Flags:** " + ", ".join(f"`{f}`" for f in flags))

    with st.expander("Extracted invoice data"):
        st.json(invoice)


def fetch_processed_invoices() -> list[dict]:
    docs = database.collection("processed_invoices").order_by(
        "processed_at", direction="DESCENDING"
    ).limit(50).stream() # stream gets results back from query as a generator of DocumentSnapshot objects one at a time

    invoices = []
    for doc in docs:
        data = doc.to_dict()
        if data is None:
            continue
        data["_doc_id"] = doc.id # fall back display name if invoice no missing
        invoices.append(data)
    return invoices


def fetch_reasoning_chain(invoice_number: str) -> list[dict]:
    steps = database.collection("reasoning_chains").document(invoice_number).collection(
        "steps"
    ).order_by("timestamp").stream()

    return [step.to_dict() for step in steps if step.to_dict() is not None]


upload_tab, review_tab = st.tabs(["Upload Invoice", "Review Queue"])

with upload_tab:
    st.write("Upload an invoice (PDF/Image) to extract and reconcile it against purchase order records.")

    uploaded_file = st.file_uploader(
        "Choose an invoice file",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
    )

    if uploaded_file is not None:
        if st.button("Reconcile", type="primary"):
            with st.spinner("Extracting and reconciling..."):
                try:
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                    response = requests.post(f"{API_URL}/reconcile", files=files, timeout=60)

                    if response.status_code == 200:
                        render_classification(response.json())
                    else:
                        try:
                            detail = response.json().get("detail", response.text)
                        except ValueError:
                            detail = response.text
                        st.error(f"Request failed ({response.status_code}): {detail}")

                except requests.exceptions.ConnectionError:
                    st.error(
                        f"Could not reach the API at {API_URL}. "
                        "Is the FastAPI backend running?"
                    )
                except requests.exceptions.Timeout:
                    st.error("Request timed out. The agent may be taking longer than expected.")

with review_tab:
    st.write("Previously processed invoices, most recent first.")

    if st.button("Refresh"):
        st.rerun()

    invoices = fetch_processed_invoices()

    if not invoices:
        st.info("No processed invoices yet. Upload one to get started.")
    else:
        for invoice in invoices:
            classification = invoice.get("classification", {})
            decision = classification.get("decision", "Unknown")
            invoice_number = invoice.get("invoice_number", invoice["_doc_id"])
            supplier = invoice.get("supplier", "Unknown supplier")

            with st.expander(f"{invoice_number} — {supplier} — {decision}"):
                col1, col2 = st.columns(2)

                with col1:
                    render_decision_banner(decision)
                    st.write(f"**Amount:** £{invoice.get('invoice_amount', 'N/A')}")
                    st.write(f"**PO Reference:** {invoice.get('po_reference') or 'N/A'}")
                    st.write(f"**Date:** {invoice.get('date', 'N/A')}")
                    st.write(f"**Reasoning:** {classification.get('reasoning', 'N/A')}")

                    flags = classification.get("flags", [])
                    if flags:
                        st.write("**Flags:** " + ", ".join(f"`{f}`" for f in flags))

                with col2:
                    st.write("**Reasoning Chain (tool calls)**")
                    steps = fetch_reasoning_chain(invoice_number)

                    if not steps:
                        st.write("_No reasoning chain recorded._") # underline
                    else:
                        for i, step in enumerate(steps, 1):
                            st.write(f"{i}. `{step.get('tool_name', 'unknown')}`")
                            st.json(step.get("result", {}), expanded=False)