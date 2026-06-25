#!/usr/bin/env python
"""Seed demo orders into the platform database.

Creates ~20 realistic pharma distributor orders covering all shipment-mode
decision scenarios the AI agent will face.  Safe to run multiple times
(idempotent — skips orders whose order_ref already exists).

Usage:
    uv run python scripts/seed_orders.py
    docker compose exec api uv run python scripts/seed_orders.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from agri_agent.config.settings import settings
from agri_agent.db.models import Order

# Use sync DB URL for the seed script
engine = create_engine(settings.database_url_sync)

today = date.today()


def d(days: int) -> date:
    """Return a date that is `days` from today."""
    return today + timedelta(days=days)


# Each tuple: (ref, retailer, medicine, qty, unit_price, margin_pct, due_date)
# Expected mode is a comment — what the AI should decide.
ORDERS = [
    # ── Very urgent + high value → air ───────────────────────────────────────
    ("ORD-2026-001", "MedPlus Delhi",       "Amoxicillin 500mg",  500, 28.40, 18.0, d(1)),
    ("ORD-2026-002", "Apollo Pharmacy",     "Insulin Glargine",   200, 54.00, 20.0, d(1)),
    # ── Very urgent + low value → train ──────────────────────────────────────
    ("ORD-2026-003", "Sai Medical Stores",  "Paracetamol 650mg", 1000,  2.20, 12.0, d(1)),
    ("ORD-2026-004", "HealthFirst Retail",  "Cetirizine 10mg",    800,  1.80, 10.0, d(2)),
    # ── Near deadline (3-5d) + high value → train ────────────────────────────
    ("ORD-2026-005", "Wellness Forever",    "Metformin 500mg",   1200,  8.50, 15.0, d(3)),
    ("ORD-2026-006", "Noble Chemists",      "Atorvastatin 20mg",  600, 18.00, 17.0, d(3)),
    ("ORD-2026-007", "Lifeline Pharmacy",   "Azithromycin 500mg", 400, 24.00, 22.0, d(4)),
    # ── Near deadline + high margin → upgrade road→train ─────────────────────
    ("ORD-2026-008", "CureWell Medicals",   "Omeprazole 20mg",    900,  6.50, 28.0, d(4)),
    ("ORD-2026-009", "PharmaEase",          "Pantoprazole 40mg",  700,  9.00, 30.0, d(5)),
    # ── Comfortable timeline → road ──────────────────────────────────────────
    ("ORD-2026-010", "City Chemist",        "Vitamin D3 1000IU", 2000,  4.00, 14.0, d(8)),
    ("ORD-2026-011", "Raj Medicals",        "Calcium Carbonate", 1500,  3.50, 11.0, d(9)),
    ("ORD-2026-012", "Sunrise Pharmacy",    "Iron Sucrose 100mg", 300, 12.00, 16.0, d(10)),
    ("ORD-2026-013", "BestCare Retail",     "Multivitamin Tab",  3000,  1.50,  9.0, d(12)),
    ("ORD-2026-014", "Greenleaf Medical",   "Zinc Sulphate 50mg",2500,  2.00, 13.0, d(14)),
    # ── Comfortable + high margin → upgrade road→train ────────────────────────
    ("ORD-2026-015", "Prime Pharmacy",      "Rosuvastatin 10mg",  500, 22.00, 32.0, d(7)),
    ("ORD-2026-016", "Excel Medicals",      "Dapagliflozin 10mg", 300, 45.00, 35.0, d(8)),
    # ── Mixed edge cases ──────────────────────────────────────────────────────
    ("ORD-2026-017", "National Pharma",     "Amlodipine 5mg",    1800,  5.50, 19.0, d(2)),  # urgent + low val → train
    ("ORD-2026-018", "Medicity Stores",     "Losartan 50mg",      800, 14.00, 24.0, d(5)),  # borderline
    ("ORD-2026-019", "LifeCare Hub",        "Montelukast 10mg",   600, 11.00, 21.0, d(3)),  # near deadline
    ("ORD-2026-020", "Alpha Pharmaceuticals","Clopidogrel 75mg",  400, 32.00, 26.0, d(6)),  # high margin upgrade
]


def seed() -> None:
    with Session(engine) as session:
        created = 0
        skipped = 0
        for ref, retailer, medicine, qty, unit_price, margin, due in ORDERS:
            exists = session.execute(
                text("SELECT 1 FROM orders WHERE order_ref = :ref"), {"ref": ref}
            ).first()
            if exists:
                skipped += 1
                continue

            order = Order(
                order_ref=ref,
                retailer_name=retailer,
                medicine_name=medicine,
                quantity=qty,
                unit_price_usd=unit_price,
                order_amount_usd=round(qty * unit_price, 2),
                margin_percent=margin,
                due_date=due,
                status="pending",
            )
            session.add(order)
            created += 1

        session.commit()
        print(f"Orders seeded: {created} created, {skipped} already existed.")


if __name__ == "__main__":
    seed()
