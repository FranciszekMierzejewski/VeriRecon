import json
import random
from datetime import datetime, timedelta
from google.cloud import firestore

database = firestore.Client(project='veriricon-hackathon')

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
    "Autodoc Truck Parts UK",
]


VAT_RATE = 0.20 # per UK standard


def generate_purchase_order(n = 25):
    """
    Generate clean purchase order records for agent to check invoices against.
    """

    purchase_order_list = []

    for i in range(n):
        number_of_lines = random.randint(1, 15)

        line_content = [
            {
                "description" : f"Item {j}",
                "quantity" : (quantity := random.randint(1, 300)),
                "unit_price" : (unit_price := round(random.randint(5, 2000), 2)),
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
            "reference" : f"PO-2026{1000+i}", # min 4 digit PO number
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
