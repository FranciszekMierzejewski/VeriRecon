import json
import random
from typing import Any
from datetime import datetime, timedelta
from google.cloud import firestore

database = firestore.Client(project='verirecon-hackathon')

SUPPLIERS = [
    "HGV Direct Ltd",
    "TRP Truck & Trailer Parts",
    "TruckSpares365",
    "Partic Motor Spares",
    "Bison Parts",
    "Truckstop Group",
    "Imexpart Limited",
    "3G Truck and Trailer Parts",
    "Clifford Thames",
    "Autodoc Truck Parts UK"
]


VAT_RATE = 0.20 # per UK standard

def generate_fake_po_ref(real_po_refs: set[str], year: int = 2026) -> str:
    """Generate PO reference that does not exist in our real PO references, using their same format to avoid bias."""

    while True:
        candidate = f"PO-{year}{random.randint(10000, 99999)}"

        if candidate not in real_po_refs:
            return candidate


def generate_unique_invoice_number(used_numbers: set[str]) -> str:
    """Generate invoice number that is guaranteed to be unique within batch"""

    while True:
        candidate = f"INV-{random.randint(10000, 99999)}"
        if candidate not in used_numbers:
            used_numbers.add(candidate)
            return candidate


def generate_purchase_order(n: int = 25) -> list[dict[str, Any]]:
    """
    Generate clean purchase order records for agent to check invoices against.
    """

    purchase_order_list: list[dict[str, Any]] = []

    for i in range(n):
        number_of_lines = random.randint(1, 15)

        line_content = [
            {
                "description" : f"Item {j}",
                "quantity" : (quantity := random.randint(1, 300)),
                "unit_price" : (unit_price := round(random.uniform(5, 2000), 2)),
                "line_total" : round(quantity * unit_price, 2) 
            }
            for j in range(number_of_lines)
        ]

        # Overall PO statistics
        subtotal = round(sum(line["line_total"] for line in line_content), 2)
        shipping_cost = round(random.uniform(0, 20), 2)
        other_charges = round(random.uniform(0, 10), 2)
        tax_amount = round(subtotal * VAT_RATE, 2)
        total = round(subtotal + shipping_cost + other_charges + tax_amount, 2)


        purchase_order = {
            "reference" : f"PO-2026{1000 + i}", # min 4 digit PO number
            "supplier" : random.choice(SUPPLIERS),
            "amount" : total,
            "line_content" : line_content,
            "status" : "open",
            "created_date" : (
                datetime(2026, 1, 1) + timedelta(days = random.randint(0, 200))
            ).strftime("%d/%m/%Y"), # DD/MM/YYYY
            "currency" : "GBP",
            "country" : "GB",
            "subtotal" : subtotal,
            "shipping_cost" : shipping_cost,
            "other_charges" : other_charges,
            "tax_rate" : VAT_RATE,
            "tax_amount" : tax_amount,
            "total" : total
        }
        purchase_order_list.append(purchase_order)

    return purchase_order_list


def generate_vendor_history(suppliers: list[str]) -> dict[str, dict[str, Any]]:
    """
    Generate aggregate historical statistics per supplier, to be used by the agent to use history for context during classification.
    """

    vendor_history: dict[str, dict[str, Any]] = {}

    for supplier in suppliers:
        invoice_count = random.randint(5, 50)
        average_invoice_amount = round(random.uniform(200, 5000), 2)

        vendor_history[supplier] = {
            "supplier" : supplier,
            "average_income_amount" : average_invoice_amount,
            "invoice_count" : invoice_count,
            "past_flags_count" : random.randint(0,4),
            "ytd_spend" : round(average_invoice_amount * invoice_count * random.uniform(0.8, 1.2), 2),
            "payment_terms" : random.choice(["Net 30", "Net 45", "Net 60"]), # due date for payment
            "currency" : "GBP"
        }

    return vendor_history


def generate_invoices_with_discrepancies(purchase_order_list: list[dict[str, Any]], count: int = 10) -> list[dict[str, Any]]:
    """
    Generates invoices with planted discrepancies with range of results
        1. Clean Match = Auto approve
        2. Minor Price Variance = Flag for review
        3. No matching PO = escalate
        4. Duplicate invoice = escalate
    """

    invoice_list: list[dict[str, Any]] = []
    real_po_refs = {po['reference'] for po in purchase_order_list}
    used_invoice_numbers: set[str] = set()

    # Case 1, 4 clean matches
    for purchase_order in random.sample(purchase_order_list, 4):
        invoice_list.append({
            "invoice_number": generate_unique_invoice_number(used_invoice_numbers),
            "supplier": purchase_order["supplier"],
            "po_reference": purchase_order["reference"],
            "invoice_amount": purchase_order["total"],
            "currency": "GBP",
            "date": datetime.now().strftime("%d/%m/%Y"),
            "expected_classification": "Auto Approve"
        })

    # Case 2, 3 minor price variations
    for purchase_order in random.sample(purchase_order_list, 3):
        variance_pct = random.uniform(0.02, 0.05)
        # direction = random.choice(["-", "+"])
        invoice_amount = round(purchase_order['total'] * (1 + variance_pct), 2) # 1 way, fraud detection

        # if direction == '-':
        #     invoice_amount = round(purchase_order['total'] * (1 - variance_pct), 2)
        # else:
        #     invoice_amount = round(purchase_order["total"] * (1 + variance_pct), 2)
        
        invoice_list.append({
            "invoice_number": generate_unique_invoice_number(used_invoice_numbers),
            "supplier": purchase_order["supplier"],
            "po_reference": purchase_order["reference"],
            "invoice_amount" : invoice_amount,
            "currency": "GBP",
            "date": datetime.now().strftime("%d/%m/%Y"),
            "expected_classification": "Flag For Review"
        })


    # Case 3, 2 non-matching purchase orders
    for _ in range(2):
        invoice_list.append({
            "invoice_number": generate_unique_invoice_number(used_invoice_numbers), 
            "supplier": random.choice(SUPPLIERS),
            "po_reference": generate_fake_po_ref(real_po_refs),
            "invoice_amount" : round(random.uniform(200, 5000), 2),
            "currency": "GBP",
            "date": datetime.now().strftime("%d/%m/%Y"),
            "expected_classification": "Escalate"
        })

    # Case 4, 1 dupe invoice
    duplicate_purchase_order = random.choice(purchase_order_list) # random selection from list
    duplicate_amount = duplicate_purchase_order['total']
    duplicate_date = datetime.now().strftime('%d/%m/%Y')

    for _ in range(2):
        invoice_list.append({
            "invoice_number": generate_unique_invoice_number(used_invoice_numbers), 
            "supplier": duplicate_purchase_order['supplier'],
            "po_reference": duplicate_purchase_order["reference"],
            "invoice_amount" : duplicate_amount,
            "currency": "GBP",
            "date": duplicate_date,
            "expected_classification": "Escalate"
        })

    random.shuffle(invoice_list) # shuffle to avoid bias of order
    return invoice_list[:count] if count < len(invoice_list) else invoice_list # count > 10 = return 10 len else < 10 len


def seed_firestore(purchase_order_list: list[dict[str, Any]], vendor_history: dict[str, dict[str, Any]]) -> None:
    """
    Write purchase orders and vendor history to Firestore. Invoices are saved to a local JSON file to simulate uploads during live demo.
    """

    batch = database.batch()

    for purchase_order in purchase_order_list:
        ref = database.collection("purchase_orders").document(purchase_order['reference'])
        batch.set(ref, purchase_order)

    for supplier, stats in vendor_history.items():
        document_id = supplier.replace(" ", "_").replace("&", "and")
        ref = database.collection("vendor_history").document(document_id)
        batch.set(ref, stats)

    batch.commit()

    print(f"Seeded {len(purchase_order_list)} purchase orders")
    print(f"Seeded {len(vendor_history)} vendor history records")


if __name__ == "__main__":
    po_list = generate_purchase_order(25)
    vendor_hist = generate_vendor_history(SUPPLIERS)
    invoice_list = generate_invoices_with_discrepancies(po_list, count=10)

    seed_firestore(po_list, vendor_hist)

    with open("data/demo_invoices.json", "w") as f:
        json.dump(invoice_list, f, indent=2)

    print(f"Generated {len(invoice_list)} demo invoices -> data/demo_invoices.json")
    print("\nExpected classifications:")
    for invoice in invoice_list:
        print(f"  {invoice['invoice_number']}: {invoice['expected_classification']}")