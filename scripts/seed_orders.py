#!/usr/bin/env python
"""Seed demo orders into the platform database.

Creates 20 realistic pharma distributor orders covering all shipment-mode
decision scenarios the AI agent will face. Each run generates a fresh batch
with a unique time-based prefix (e.g. ORD-0626-1430-001) so orders are
always inserted as new pending records regardless of existing data.

Usage:
    uv run python scripts/seed_orders.py
    docker compose exec api uv run python scripts/seed_orders.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agri_agent.config.settings import settings
from agri_agent.db.models import Order

engine = create_engine(settings.database_url_sync)

today = date.today()


def d(days: int) -> date:
    return today + timedelta(days=days)


# Template rows — (retailer, medicine, qty, unit_price, margin_pct, due_days)
# order_ref is generated per-run using a batch prefix.
ORDER_TEMPLATES = [
    # ── Very urgent + high value → air ───────────────────────────────────────
    ("MedPlus Delhi",          "Amoxicillin 500mg",    500, 28.40, 18.0, 1),
    ("Apollo Pharmacy",        "Insulin Glargine",     200, 54.00, 20.0, 1),
    # ── Very urgent + low value → train ──────────────────────────────────────
    ("Sai Medical Stores",     "Paracetamol 650mg",   1000,  2.20, 12.0, 1),
    ("HealthFirst Retail",     "Cetirizine 10mg",      800,  1.80, 10.0, 2),
    # ── Near deadline (3-5d) + high value → train ────────────────────────────
    ("Wellness Forever",       "Metformin 500mg",     1200,  8.50, 15.0, 3),
    ("Noble Chemists",         "Atorvastatin 20mg",    600, 18.00, 17.0, 3),
    ("Lifeline Pharmacy",      "Azithromycin 500mg",   400, 24.00, 22.0, 4),
    # ── Near deadline + high margin → upgrade road→train ─────────────────────
    ("CureWell Medicals",      "Omeprazole 20mg",      900,  6.50, 28.0, 4),
    ("PharmaEase",             "Pantoprazole 40mg",    700,  9.00, 30.0, 5),
    # ── Comfortable timeline → road ──────────────────────────────────────────
    ("City Chemist",           "Vitamin D3 1000IU",   2000,  4.00, 14.0, 8),
    ("Raj Medicals",           "Calcium Carbonate",   1500,  3.50, 11.0, 9),
    ("Sunrise Pharmacy",       "Iron Sucrose 100mg",   300, 12.00, 16.0, 10),
    ("BestCare Retail",        "Multivitamin Tab",    3000,  1.50,  9.0, 12),
    ("Greenleaf Medical",      "Zinc Sulphate 50mg",  2500,  2.00, 13.0, 14),
    # ── Comfortable + high margin → upgrade road→train ────────────────────────
    ("Prime Pharmacy",         "Rosuvastatin 10mg",    500, 22.00, 32.0, 7),
    ("Excel Medicals",         "Dapagliflozin 10mg",   300, 45.00, 35.0, 8),
    # ── Mixed edge cases ──────────────────────────────────────────────────────
    ("National Pharma",        "Amlodipine 5mg",      1800,  5.50, 19.0, 2),
    ("Medicity Stores",        "Losartan 50mg",        800, 14.00, 24.0, 5),
    ("LifeCare Hub",           "Montelukast 10mg",     600, 11.00, 21.0, 3),
    ("Alpha Pharmaceuticals",  "Clopidogrel 75mg",     400, 32.00, 26.0, 6),
]


def seed() -> None:
    # Unique prefix for this run — MMDD-HHMMSS — unique per second, readable
    batch = datetime.now().strftime("%m%d-%H%M%S")

    with Session(engine) as session:
        for i, (retailer, medicine, qty, unit_price, margin, due_days) in enumerate(ORDER_TEMPLATES, start=1):
            order_ref = f"ORD-{batch}-{i:03d}"
            session.add(Order(
                order_ref=order_ref,
                retailer_name=retailer,
                medicine_name=medicine,
                quantity=qty,
                unit_price_usd=unit_price,
                order_amount_usd=round(qty * unit_price, 2),
                margin_percent=margin,
                due_date=d(due_days),
                status="pending",
            ))

        session.commit()
        print(f"Orders seeded: {len(ORDER_TEMPLATES)} new pending orders created (batch: {batch}).")


if __name__ == "__main__":
    seed()
