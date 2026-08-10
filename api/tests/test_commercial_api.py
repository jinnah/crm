"""Quotes, invoices, payments and receipts: money math, immutability,
idempotency and audited corrections."""

import hashlib
import uuid

import pytest

from app.models import CommercialDocument, CommercialDocumentVersion, utcnow
from app.services import commercial as commercial_service
from tests.conftest import csrf_headers, login
from tests.test_jobs_api import create_job, make_lead, owner_session

LINES = [
    {
        "description": "Shingle replacement",
        "quantity_milli": 2500,  # 2.5 squares
        "unit": "sq",
        "unit_price_minor": 19999,  # 199.99
        "discount_bp": 1000,  # 10% line discount
        "tax_rate_bp": 825,  # 8.25%
    },
    {
        "description": "Disposal fee",
        "quantity_milli": 1000,
        "unit": "",
        "unit_price_minor": 5000,
        "discount_bp": 0,
        "tax_rate_bp": 0,
    },
]


@pytest.fixture()
def job_setup(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db, name="Pat Customer", email="pat@customer.test")
    job = create_job(client, headers, lead.id)
    return headers, lead, job


def make_quote(client, headers, job_id, lines=None, discount_bp=0):
    draft = client.post(
        f"/api/v1/jobs/{job_id}/commercial", json={"kind": "quote"}, headers=headers
    )
    assert draft.status_code == 201, draft.text
    quote = draft.json()
    updated = client.patch(
        f"/api/v1/jobs/{job_id}/commercial/{quote['id']}",
        json={"lines": lines or LINES, "discount_bp": discount_bp},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    return updated.json()


def issue(client, headers, job_id, document_id):
    response = client.post(f"/api/v1/jobs/{job_id}/commercial/{document_id}/issue", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_totals_use_documented_fixed_precision_rounding(client, db, job_setup):
    headers, _lead, job = job_setup
    quote = make_quote(client, headers, job["id"], discount_bp=500)  # 5% document discount

    # line 1: 2.5 × 19999 × 0.9  = 44997.75 → 44998
    # line 2: 1 × 5000           = 5000
    # subtotal                   = 49998
    # discount 5%                = 2499.90 → 2500
    # tax: 44998 × 0.95 × 0.0825 = 3526.71... → 3527
    # total = 49998 − 2500 + 3527 = 51025
    assert quote["lines"][0]["line_total_minor"] == 44998
    assert quote["subtotal_minor"] == 49998
    assert quote["discount_total_minor"] == 2500
    assert quote["tax_total_minor"] == 3527
    assert quote["total_minor"] == 51025


def test_browser_supplied_totals_are_ignored(client, db, job_setup):
    headers, _lead, job = job_setup
    draft = client.post(
        f"/api/v1/jobs/{job['id']}/commercial", json={"kind": "quote"}, headers=headers
    ).json()
    forged = client.patch(
        f"/api/v1/jobs/{job['id']}/commercial/{draft['id']}",
        json={
            "lines": [
                {
                    "description": "Cheap trick",
                    "quantity_milli": 1000,
                    "unit_price_minor": 10000,
                    "total_minor": 1,  # unknown field: ignored by the schema
                }
            ],
            "discount_bp": 0,
            "subtotal_minor": 1,
            "total_minor": 1,
        },
        headers=headers,
    )
    assert forged.status_code == 200
    assert forged.json()["total_minor"] == 10000


def test_invalid_lines_are_rejected(client, db, job_setup):
    headers, _lead, job = job_setup
    draft = client.post(
        f"/api/v1/jobs/{job['id']}/commercial", json={"kind": "quote"}, headers=headers
    ).json()
    for bad_line in (
        {"description": "Negative qty", "quantity_milli": -1000, "unit_price_minor": 100},
        {"description": "Negative price", "quantity_milli": 1000, "unit_price_minor": -5},
        {
            "description": "Silly discount",
            "quantity_milli": 1000,
            "unit_price_minor": 100,
            "discount_bp": 20000,
        },
        {
            "description": "Silly tax",
            "quantity_milli": 1000,
            "unit_price_minor": 100,
            "tax_rate_bp": 9000,
        },
    ):
        response = client.patch(
            f"/api/v1/jobs/{job['id']}/commercial/{draft['id']}",
            json={"lines": [bad_line], "discount_bp": 0},
            headers=headers,
        )
        assert response.status_code == 422, bad_line["description"]


def test_issue_assigns_number_and_immutable_version(client, db, app, job_setup):
    headers, _lead, job = job_setup
    quote = make_quote(client, headers, job["id"])
    issued = issue(client, headers, job["id"], quote["id"])

    year = utcnow().year
    assert issued["number"] == f"Q-{year}-0001"
    assert issued["status"] == "sent"
    assert issued["current_version"] == 1

    versions = client.get(f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/versions").json()
    assert len(versions) == 1
    first = versions[0]

    # The stored PDF is byte-for-byte stable and matches its recorded hash.
    pdf_one = client.get(f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/versions/1/pdf")
    pdf_two = client.get(f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/versions/1/pdf")
    assert pdf_one.status_code == 200
    assert pdf_one.content == pdf_two.content
    assert hashlib.sha256(pdf_one.content).hexdigest() == first["pdf_sha256"]
    assert b"%PDF" in pdf_one.content[:8]

    # The job advanced automatically to quoted.
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "quoted"


def test_reissuing_a_quote_supersedes_without_rewriting(client, db, job_setup):
    headers, _lead, job = job_setup
    quote = make_quote(client, headers, job["id"])
    issue(client, headers, job["id"], quote["id"])
    first_pdf = client.get(
        f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/versions/1/pdf"
    ).content

    cheaper = [dict(LINES[0], unit_price_minor=17999), LINES[1]]
    updated = client.patch(
        f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}",
        json={"lines": cheaper, "discount_bp": 0},
        headers=headers,
    )
    assert updated.status_code == 200
    reissued = issue(client, headers, job["id"], quote["id"])
    assert reissued["current_version"] == 2
    assert reissued["number"].startswith("Q-")  # number never changes

    versions = client.get(f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[1]["superseded_at"] is not None
    # v1 bytes unchanged after the correction.
    still_first = client.get(
        f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/versions/1/pdf"
    ).content
    assert still_first == first_pdf


def test_issued_invoice_is_immutable(client, db, job_setup):
    headers, _lead, job = job_setup
    draft = client.post(
        f"/api/v1/jobs/{job['id']}/commercial", json={"kind": "invoice"}, headers=headers
    ).json()
    client.patch(
        f"/api/v1/jobs/{job['id']}/commercial/{draft['id']}",
        json={"lines": LINES, "discount_bp": 0},
        headers=headers,
    )
    issue(client, headers, job["id"], draft["id"])

    edited = client.patch(
        f"/api/v1/jobs/{job['id']}/commercial/{draft['id']}",
        json={"lines": LINES, "discount_bp": 0},
        headers=headers,
    )
    assert edited.status_code == 409
    again = client.post(f"/api/v1/jobs/{job['id']}/commercial/{draft['id']}/issue", headers=headers)
    assert again.status_code == 409


def _respond(db, settings, document_id, *, accept, name="Pat Customer"):
    document = db.get(CommercialDocument, uuid.UUID(document_id))
    version = (
        db.query(CommercialDocumentVersion)
        .filter_by(document_id=document.id, superseded_at=None)
        .one()
    )
    return commercial_service.respond_to_quote(
        db, document.id, version, accept=accept, typed_name=name
    )


def test_quote_response_is_idempotent_with_one_winner(client, db, job_setup):
    from app.config import get_settings

    headers, _lead, job = job_setup
    quote = make_quote(client, headers, job["id"])
    issue(client, headers, job["id"], quote["id"])
    settings = get_settings()

    accepted = _respond(db, settings, quote["id"], accept=True)
    db.commit()
    assert accepted.status_status if False else accepted.status == "accepted"
    assert accepted.response_snapshot_sha256

    # Identical retry replays; the opposite response is refused.
    replay = _respond(db, settings, quote["id"], accept=True)
    db.commit()
    assert replay.status == "accepted"
    with pytest.raises(commercial_service.CommercialError):
        _respond(db, settings, quote["id"], accept=False)
    db.rollback()

    # Acceptance advanced the job, but scheduled nothing and paid nothing.
    job_state = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert job_state["status"] == "approved"
    assert job_state["scheduled_for"] is None


def test_quote_conversion_is_idempotent(client, db, job_setup):
    from app.config import get_settings

    headers, _lead, job = job_setup
    quote = make_quote(client, headers, job["id"])
    issue(client, headers, job["id"], quote["id"])
    _respond(db, get_settings(), quote["id"], accept=True)
    db.commit()

    first = client.post(
        f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/convert", headers=headers
    )
    assert first.status_code == 200, first.text
    invoice = first.json()
    assert invoice["kind"] == "invoice"
    assert invoice["source_quote_id"] == quote["id"]
    assert invoice["total_minor"] == first.json()["total_minor"]

    retry = client.post(
        f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/convert", headers=headers
    )
    assert retry.status_code == 200
    assert retry.json()["id"] == invoice["id"]  # one invoice, ever

    invoices = [
        d
        for d in client.get(f"/api/v1/jobs/{job['id']}/commercial").json()
        if d["kind"] == "invoice"
    ]
    assert len(invoices) == 1


def make_paid_invoice(client, db, headers, job_id, *, lines=None):
    draft = client.post(
        f"/api/v1/jobs/{job_id}/commercial", json={"kind": "invoice"}, headers=headers
    ).json()
    client.patch(
        f"/api/v1/jobs/{job_id}/commercial/{draft['id']}",
        json={"lines": lines or LINES, "discount_bp": 0},
        headers=headers,
    )
    return issue(client, headers, job_id, draft["id"])


def record_payment(client, headers, job_id, invoice_id, amount, key, **overrides):
    payload = {
        "amount_minor": amount,
        "currency": "USD",
        "method": "check",
        "paid_on": utcnow().isoformat(),
        "idempotency_key": key,
        **overrides,
    }
    return client.post(
        f"/api/v1/jobs/{job_id}/commercial/{invoice_id}/payments",
        json=payload,
        headers=headers,
    )


def test_partial_payments_receipts_and_balance_guard(client, db, job_setup):
    headers, _lead, job = job_setup
    invoice = make_paid_invoice(client, db, headers, job["id"])
    total = invoice["total_minor"]

    first = record_payment(client, headers, job["id"], invoice["id"], 10000, "pay-key-0001")
    assert first.status_code == 201, first.text
    payment = first.json()
    assert payment["receipt_document_id"] is not None

    state = [
        d
        for d in client.get(f"/api/v1/jobs/{job['id']}/commercial").json()
        if d["id"] == invoice["id"]
    ][0]
    assert state["status"] == "partially_paid"
    assert state["amount_paid_minor"] == 10000

    # A receipt exists as an immutable issued document with its own number.
    receipt = [
        d
        for d in client.get(f"/api/v1/jobs/{job['id']}/commercial").json()
        if d["kind"] == "receipt"
    ][0]
    assert receipt["number"].startswith("R-")
    assert receipt["total_minor"] == 10000

    # Overpayment is refused, never silent credit.
    over = record_payment(client, headers, job["id"], invoice["id"], total, "pay-key-0002")
    assert over.status_code == 409

    rest = record_payment(client, headers, job["id"], invoice["id"], total - 10000, "pay-key-0003")
    assert rest.status_code == 201
    state = [
        d
        for d in client.get(f"/api/v1/jobs/{job['id']}/commercial").json()
        if d["id"] == invoice["id"]
    ][0]
    assert state["status"] == "paid"


def test_duplicate_payment_returns_same_payment_and_receipt(client, db, job_setup):
    headers, _lead, job = job_setup
    invoice = make_paid_invoice(client, db, headers, job["id"])

    first = record_payment(client, headers, job["id"], invoice["id"], 5000, "replay-key-01")
    retry = record_payment(client, headers, job["id"], invoice["id"], 5000, "replay-key-01")
    assert first.status_code == 201 and retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    assert first.json()["receipt_document_id"] == retry.json()["receipt_document_id"]

    receipts = [
        d
        for d in client.get(f"/api/v1/jobs/{job['id']}/commercial").json()
        if d["kind"] == "receipt"
    ]
    assert len(receipts) == 1


def test_payment_reversal_preserves_receipt_history(client, db, job_setup):
    headers, _lead, job = job_setup
    invoice = make_paid_invoice(client, db, headers, job["id"])
    payment = record_payment(
        client, headers, job["id"], invoice["id"], 8000, "reverse-key-01"
    ).json()

    reversed_ = client.post(
        f"/api/v1/jobs/{job['id']}/payments/{payment['id']}/reverse",
        json={"reason": "check bounced"},
        headers=headers,
    )
    assert reversed_.status_code == 200
    assert reversed_.json()["voided_at"] is not None
    assert reversed_.json()["void_reason"] == "check bounced"

    documents = client.get(f"/api/v1/jobs/{job['id']}/commercial").json()
    receipt = [d for d in documents if d["kind"] == "receipt"][0]
    assert receipt["status"] == "voided"  # marked void, never erased
    state = [d for d in documents if d["id"] == invoice["id"]][0]
    assert state["amount_paid_minor"] == 0
    assert state["status"] in ("sent", "viewed", "overdue")


def test_payment_fields_reject_card_credentials(client, db, job_setup):
    headers, _lead, job = job_setup
    invoice = make_paid_invoice(client, db, headers, job["id"])

    pan = record_payment(
        client,
        headers,
        job["id"],
        invoice["id"],
        1000,
        "pan-key-00001",
        reference="card 4111 1111 1111 1111",
    )
    assert pan.status_code == 400
    assert "card" in pan.json()["detail"].lower()

    cvv = record_payment(
        client,
        headers,
        job["id"],
        invoice["id"],
        1000,
        "cvv-key-00001",
        internal_note="CVV 123 for later",
    )
    assert cvv.status_code == 400

    # An honest check number sails through.
    ok = record_payment(
        client,
        headers,
        job["id"],
        invoice["id"],
        1000,
        "ok-key-000001",
        reference="check #2041",
    )
    assert ok.status_code == 201


def test_voided_invoice_refuses_payments_and_stays_in_history(client, db, job_setup):
    headers, _lead, job = job_setup
    invoice = make_paid_invoice(client, db, headers, job["id"])
    voided = client.post(
        f"/api/v1/jobs/{job['id']}/commercial/{invoice['id']}/void",
        json={"reason": "wrong customer"},
        headers=headers,
    )
    assert voided.status_code == 200
    assert voided.json()["status"] == "voided"

    refused = record_payment(client, headers, job["id"], invoice["id"], 1000, "void-key-0001")
    assert refused.status_code == 409
    assert any(
        d["id"] == invoice["id"] for d in client.get(f"/api/v1/jobs/{job['id']}/commercial").json()
    )


def test_team_members_cannot_issue_void_or_record_payments(client, db, make_user):
    headers = owner_session(client, make_user)
    member = make_user(email="member@example.com", role="team_member")
    lead = make_lead(db, assigned_to=member.id)
    job = create_job(client, headers, lead.id)
    quote = make_quote(client, headers, job["id"])
    invoice = make_paid_invoice(client, db, headers, job["id"])
    client.cookies.clear()

    member_headers = csrf_headers(login(client, "member@example.com"))
    # Drafting is allowed on their own jobs…
    draft = client.post(
        f"/api/v1/jobs/{job['id']}/commercial", json={"kind": "quote"}, headers=member_headers
    )
    assert draft.status_code == 201
    # …but issuing, voiding, converting, paying and sending are not.
    assert (
        client.post(
            f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/issue", headers=member_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/jobs/{job['id']}/commercial/{invoice['id']}/void",
            json={"reason": "x"},
            headers=member_headers,
        ).status_code
        == 403
    )
    assert (
        record_payment(
            client, member_headers, job["id"], invoice["id"], 1000, "member-key-01"
        ).status_code
        == 403
    )
