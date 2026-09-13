"""
ADK agent for autonomous invoice reconciliation.

Given one extracted invoice, the agent decides which tools in agents/tools.py to call and in what order, to arrive at a classification.
Each tool call is logged to Firestore's reasoning_chains collection as it happens to act as an audit trail - hence being legible to a human reviewer
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agent import tools as recon_tools
from db.firestore_client import get_client

database = get_client()

MODEL_ID = "gemini-3.5-flash"
APP_NAME = "verirecon"
USER_ID = "verirecon-agent"

SYSTEM_PROMPT = """
You are an invoice reconciliation agent for a UK HGV/truck parts business.

For each invoice you are given (supplier, po_reference, invoice_amount,
currency, date, and optionally subtotal, tax_amount, shipping_cost,
other_charges), use your tools to verify it before deciding anything.
Never guess or assume a PO's amount, status, or existence — always call
lookup_po first.

Decision logic:
- If lookup_po reports found=False, there is no PO to compare against.
  Do not call compute_variance. Classify as Escalate.
- If lookup_po reports found=True, call compute_variance using the
  invoice_amount you were given and the po_amount returned by lookup_po.
- Always call check_exact_duplicate and check_fuzzy_duplicates regardless
  of the PO outcome — a valid PO does not rule out the same invoice being
  submitted twice.
- Always call verify_invoice_arithmetic using the invoice's subtotal,
  tax_amount, shipping_cost, and other_charges (pass 0 for any field not
  shown on the invoice) — this checks the invoice's own numbers are
  internally consistent, independent of whether it matches the PO.
- Optionally call get_vendor_history for extra context.

Classify as:
- "Auto Approve": PO found, within_tolerance is True, no duplicates,
  and (if checked) the invoice's arithmetic is consistent.
- "Flag For Review": PO found, NOT within_tolerance, not a duplicate,
  OR the invoice's arithmetic is inconsistent but no other issue found.
- "Escalate": PO not found, OR is_duplicate/is_fuzzy_duplicate is True.

Respond with ONLY a single JSON object, no other text, in this exact form:
{
  "decision": "Auto Approve" | "Flag For Review" | "Escalate",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<2-3 sentence plain-English explanation>",
  "flags": ["<short flag strings, e.g. 'no_po', 'price_variance', 'duplicate', 'arithmetic_inconsistent'>"]
}
""".strip()


def _clear_reasoning_chain(invoice_number: str) -> None:
    """
    Delete any existing reasoning-chain steps for this invoice before a new run starts. 
    Without this, re-processing the same invoice_number appends the new run's steps onto the old ones, 
    making the audit trail unreadable 
    """
    steps_ref = database.collection("reasoning_chains").document(invoice_number).collection("steps")
    for doc in steps_ref.stream():
        doc.reference.delete()


def _make_logging_tools(invoice_number: str) -> list[Any]:
    """
    Wrap each reconciliation tool so every call is logged to Firestore's reasoning_chains collection, along with invoice number.
    """

    # Type of tool names not Optional for AFK schema extraction
    def _log(tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]) -> None:
        database.collection("reasoning_chains").document(invoice_number).collection(
            "steps" 
        ).add({
            "tool_name" : tool_name,
            "tool_args" : tool_args,
            "result" : result,
            "timestamp" : datetime.now(timezone.utc).isoformat()
        })

    def lookup_po(po_reference: str) -> dict[str, Any]:
        """Look up a purchase order by reference. Returns found as False if it doesn't exist."""
        result = recon_tools.lookup_po(po_reference)
        _log("lookup_po", {"po_reference": po_reference}, result)
        return result

    def get_vendor_history(supplier: str) -> dict[str, Any]:
        """Retrieve historical statistics for a supplier. Returns found as False for a new supplier."""
        result = recon_tools.get_vendor_history(supplier)
        _log("get_vendor_history", {"supplier": supplier}, result)
        return result

    def compute_variance(invoice_amount: float, po_amount: float) -> dict[str, Any]:
        """Calculate the absolute GBP difference between an invoice amount and its matched PO amount."""
        result = recon_tools.compute_variance(invoice_amount, po_amount)
        _log("compute_variance", {"invoice_amount": invoice_amount, "po_amount": po_amount}, result)
        return result

    def check_exact_duplicate(supplier: str, invoice_amount: float, invoice_date: str) -> dict[str, Any]:
        """Check for a prior invoice with an exact matching supplier, amount, and date."""
        result = recon_tools.check_exact_duplicate(
            supplier, invoice_amount, invoice_date, exclude_invoice_number=invoice_number
        )
        _log(
            "check_exact_duplicate",
            {"supplier": supplier, "invoice_amount": invoice_amount, "invoice_date": invoice_date},
            result
        )
        return result

    def check_fuzzy_duplicates(supplier: str, invoice_amount: float, invoice_date: str) -> dict[str, Any]:
        """Check for near-duplicate invoices within a small amount tolerance and date window."""
        result = recon_tools.check_fuzzy_duplicates(
            supplier, invoice_amount, invoice_date, exclude_invoice_number=invoice_number
        )
        _log(
            "check_fuzzy_duplicates",
            {"supplier": supplier, "invoice_amount": invoice_amount, "invoice_date": invoice_date},
            result
        )
        return result

    def verify_invoice_arithmetic(
        invoice_amount: float,
        subtotal: float,
        tax_amount: float,
        shipping_cost: float,
        other_charges: float) -> dict[str, Any]:
        """Check whether an invoice's stated subtotal, tax, shipping, and other charges sum to its invoice_amount."""

        result = recon_tools.verify_invoice_arithmetic(
            invoice_amount, subtotal, tax_amount, shipping_cost, other_charges
        )
        _log(
            "verify_invoice_arithmetic",
            {
                "invoice_amount": invoice_amount,
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "shipping_cost": shipping_cost,
                "other_charges": other_charges,
            },
            result,
        )
        return result

    return [lookup_po, get_vendor_history, compute_variance, check_exact_duplicate, check_fuzzy_duplicates, verify_invoice_arithmetic]


def _build_agent(invoice_number: str) -> LlmAgent:
    """Build a fresh LLMAgent per invoice so its logging tools close over correct invoice"""
    return LlmAgent(
        name = "invoice_reconciliation_agent",
        model = MODEL_ID,
        instruction = SYSTEM_PROMPT,
        tools = _make_logging_tools(invoice_number)
    )


# Coroutine that orchestrates an agent run without blocking thread whilst agent works
async def _run_agent_async(invoice: dict[str, Any]) -> str:
    """Run the agent against one invoice, returning its raw final text response.
    Creates an in-memory session for an invoice so agent can maintain context during tool-use loop.
    Sends invoice JSON as user message, streams events from runner.run_async and capture agent's final response.
    Async to avoid blocking event loop.
    """
    agent = _build_agent(invoice["invoice_number"])
    session_service = InMemorySessionService()

    session_id = f"session-{invoice['invoice_number']}"
    await session_service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)

    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
    invoice_message = Content(role="user", parts=[Part(text=json.dumps(invoice))])

    final_text = "(no final response)"
    async for event in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=invoice_message):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text or final_text

    return final_text


def _parse_classification(raw_text: str) -> dict[str, Any]:
    """
    Parse the agent's final JSON response. Falls back to a safe Escalate result if parsing fails, 
    rather than crashing the whole pipeline, since unparseable classification should be flagged to human reviewer.
    """
    cleaned = raw_text.strip() # strip markdown if aroud JSON
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "decision": "Escalate",
            "confidence": 0.0,
            "reasoning": "Agent response could not be parsed as valid JSON.",
            "flags": ["unparseable_response"],
            "raw_response": raw_text
        }


def _write_processed_invoice(invoice: dict[str, Any], classification: dict[str, Any]) -> None:
    """
    Persist the processed invoice and classification. This populates processed_invoices with originals, which 
    check_exact_duplicate/check_fuzzy_duplicates depend on without which duplicate detection has 
    nothing to compare future invoices against.
    """
    database.collection("processed_invoices").document(invoice["invoice_number"]).set({
        **invoice, # unpack dictionary
        "classification": classification,
        "processed_at": datetime.now(timezone.utc).isoformat()
    })


async def reconcile_invoice_async(invoice: dict[str, Any]) -> dict[str, Any]:
    """
    Run an agent, parse its JSON, perist result. When no event loop running.

    Async version of reconcile_invoice, for use inside an already-running
    event loop, e.g. FastAPI/uvicorn. The sync reconcile_invoice()
    wraps this with asyncio.run(), which only works when no event loop is
    already active. calling it from inside an async FastAPI endpoint
    raises 'asyncio.run() cannot be called from a running event loop'.
    """

    
    # Start or continue the asynchronous operation.
    # Pause reconcile_invoice_async while _run_agent_async is waiting.
    # Allow the event loop to process other work.
    # Resume once _run_agent_async completes.
    # Assign its result to raw_text.

    _clear_reasoning_chain(invoice["invoice_number"])
    raw_text = await _run_agent_async(invoice)
    classification = _parse_classification(raw_text)
    _write_processed_invoice(invoice, classification)
    return classification


def reconcile_invoice(invoice: dict[str, Any]) -> dict[str, Any]:
    """Sync entry point for scripts/tests with no event loop already running."""
    return asyncio.run(reconcile_invoice_async(invoice))


if __name__ == "__main__":
    with open("data/demo_invoices.json") as f:
        demo_invoices = json.load(f)

    # Explicitly pick a no-PO case, not just any "Escalate" since Escalate also covers duplicate-pair case, 
    # which behaves differently since first of the two duplicate invoices processed has nothing to compare to

    test_invoice = next(inv for inv in demo_invoices if inv["invoice_number"] == "INV-56291")
    print(f"Testing: {test_invoice['invoice_number']} (expected: {test_invoice['expected_classification']})")
    print(json.dumps(reconcile_invoice(test_invoice), indent=2))