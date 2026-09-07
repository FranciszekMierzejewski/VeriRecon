"""
End-to-end test: runs every invoice in data/demo_invoices.json through the agent and compares its classification 
against expected_classification.

Integration test. Running it twice in a row will affect duplicate detection results (invoices already in
processed_invoices from a prior run may trigger is_duplicate on a second run), 
so expect the duplicate-pair case to only behave correctly on a fresh run against a clean processed_invoices collection.
"""

import json

from agent.main_agent import reconcile_invoice


def run_full_suite() -> None:
    with open("data/demo_invoices.json") as f:
        invoices = json.load(f)

    results: list[dict[str, str]] = []
    correct = 0

    for invoice in invoices:
        classification = reconcile_invoice(invoice)
        decision = classification.get("decision")
        expected = invoice["expected_classification"]
        is_correct = decision == expected
        correct += int(is_correct)

        results.append({
            "invoice_number": invoice["invoice_number"],
            "expected": expected,
            "decision": decision,
            "flags": classification.get("flags", []),
            "correct": is_correct,
        })

    print(f"\n{'Invoice':<12} {'Expected':<16} {'Decision':<16} {'Flags':<25} {'Match'}")
    print("-" * 80)
    for r in results:
        status = "✓" if r["correct"] else "✗"
        flags_str = ", ".join(r["flags"]) if r["flags"] else "-"
        print(f"{r['invoice_number']:<12} {r['expected']:<16} {r['decision'] or 'ERROR':<16} {flags_str:<25} {status}")

    print("-" * 80)
    print(f"\n{correct}/{len(invoices)} correct ({round(100 * correct / len(invoices), 1)}%)")

    mismatches = [r for r in results if not r["correct"]]
    if mismatches:
        print(f"\n{len(mismatches)} mismatch(es):")
        for m in mismatches:
            print(f"  {m['invoice_number']}: expected {m['expected']}, decision {m['decision']}")


if __name__ == "__main__":
    run_full_suite()