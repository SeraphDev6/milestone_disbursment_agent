import copy

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

GOOD_DRAFT = {
    "title": "Riverside Literacy Grant 2027",
    "total_amount": "30000.00",
    "currency": "USD",
    "settlement_mode": "escrow_release",
    "parties": [
        {"role": "payer", "paypal_email": "funder@example.org", "display_name": "Harbor Foundation"},
        {"role": "payee", "paypal_email": "grantee@example.org", "display_name": "Riverside Reads"},
    ],
    "milestones": [
        {
            "title": "Tranche 1: spring workshops",
            "amount": "10000.00",
            "due_date": "2027-05-31",
            "criteria": [
                {
                    "statement": "At least 200 people attend the spring reading workshops",
                    "measure": {
                        "metric": "workshop attendees",
                        "comparator": "at_least",
                        "target": 200,
                        "unit": "people",
                    },
                    "evidence_required": ["spreadsheet"],
                    "evidence_description": "Signed sign-in sheets for each session, one row per attendee",
                    "verification": "check",
                    "check_id": "row_count",
                },
                {
                    "statement": "Workshop spending stays within the $10,000 tranche budget",
                    "measure": {"metric": "workshop spend", "comparator": "at_most", "target": 10000, "unit": "USD"},
                    "evidence_required": ["spreadsheet"],
                    "evidence_description": "Q2 budget sheet with actual spend per line item",
                    "verification": "check",
                    "check_id": "sheet_total",
                },
            ],
        },
        {
            "title": "Tranche 2: outcomes report",
            "amount": "20000.00",
            "due_date": "2027-09-30",
            "criteria": [
                {
                    "statement": "Final report shows pre and post reading scores for every enrolled student",
                    "measure": {"metric": "students with pre and post scores", "comparator": "exists"},
                    "evidence_required": ["document", "dataset"],
                    "evidence_description": "Final outcomes report PDF with the anonymized score table",
                    "verification": "model",
                },
                {
                    "statement": "Final report is submitted on or before September 30, 2027",
                    "measure": {"metric": "final report date", "comparator": "on_or_before", "target": "2027-09-30"},
                    "evidence_required": ["document"],
                    "evidence_description": "Final outcomes report PDF with its cover date",
                    "verification": "check",
                    "check_id": "dated_by",
                },
            ],
        },
    ],
}


@pytest.fixture
def draft() -> dict:
    return copy.deepcopy(GOOD_DRAFT)


@pytest.fixture
def client():
    app = create_app(Settings(database_url="sqlite://"))
    with TestClient(app) as c:
        yield c
