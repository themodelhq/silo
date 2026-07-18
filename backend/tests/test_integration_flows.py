import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import hashlib
import hmac
import json

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["PAYSTACK_SECRET_KEY"] = "sk_test_dummy"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from app.main import app
from app import models
from app.database import get_db


# Isolated in-memory SQLite shared across the whole test session so every
# request in a test sees the same data (StaticPool keeps one connection alive).
_engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
_TestingSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
models.Base.metadata.create_all(bind=_engine)


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)

PAYSLIP_TEXT = """
COMPASS GLOBAL TECHNOLOGIES NIGERIA LTD
Employee Name: Jane Doe

EARNINGS:
Basic Salary: 300,000.00 NGN

DEDUCTIONS:
PAYE Tax: 20,000.00
Pension Deduction: 10,000.00

BUDGET SPLIT:
Rent: 100,000.00
Clothing: 20,000.00
Health: 15,000.00

NET TAKE HOME: 320,000.00
"""


@pytest.fixture(scope="module")
def auth_headers():
    client.post("/auth/register", json={"email": "jane@test.com", "password": "Passw0rd!", "full_name": "Jane Doe"})
    r = client.post("/auth/login", json={"email": "jane@test.com", "password": "Passw0rd!"})
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_admin_routes_are_forbidden_for_regular_users(auth_headers):
    r = client.get("/admin/stats", headers=auth_headers)
    assert r.status_code == 403


def test_admin_bootstrap_email_is_promoted_on_registration():
    os.environ["ADMIN_BOOTSTRAP_EMAILS"] = "boss@test.com"
    client.post("/auth/register", json={"email": "boss@test.com", "password": "Passw0rd!", "full_name": "Boss"})
    r = client.post("/auth/login", json={"email": "boss@test.com", "password": "Passw0rd!"})
    admin_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = client.get("/admin/stats", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total_users"] >= 1
    del os.environ["ADMIN_BOOTSTRAP_EMAILS"]


def test_payslip_parse_generates_matching_envelope_split(auth_headers):
    r = client.post("/payslips/parse", json={"raw_text": PAYSLIP_TEXT}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()

    names = {item["envelope_name"] for item in body["generated_envelope_split"]}
    assert names == {"Rent", "Clothing", "Health"}

    r = client.get("/envelopes/", headers=auth_headers)
    envelope_names = {e["name"] for e in r.json()}
    assert {"Rent", "Clothing", "Health"}.issubset(envelope_names)

    r = client.get("/envelopes/rules", headers=auth_headers)
    assert all(rule["active"] for rule in r.json())


def test_reuploading_a_payslip_replaces_the_previous_split(auth_headers):
    second_payslip = PAYSLIP_TEXT.replace(
        "BUDGET SPLIT:\nRent: 100,000.00\nClothing: 20,000.00\nHealth: 15,000.00",
        "BUDGET SPLIT:\nRent: 90,000.00\nSavings: 20,000.00",
    )
    r = client.post("/payslips/parse", json={"raw_text": second_payslip}, headers=auth_headers)
    assert r.status_code == 200

    r = client.get("/envelopes/rules", headers=auth_headers)
    active_names = {rule["envelope_name"] for rule in r.json() if rule["active"]}
    assert "Savings" in active_names
    assert "Clothing" not in active_names  # old split superseded, not active anymore


def test_virtual_account_creation_fails_gracefully_without_provider_key(auth_headers, monkeypatch):
    monkeypatch.setattr("app.payments.PAYSTACK_SECRET_KEY", "")
    r = client.post("/payments/accounts", json={"phone": "+2348100000000"}, headers=auth_headers)
    assert r.status_code == 502


def test_payment_webhook_splits_incoming_money_across_active_envelopes(auth_headers):
    db = _TestingSessionLocal()
    user = db.query(models.User).filter(models.User.email == "jane@test.com").first()
    account = models.PaymentAccount(
        user_id=user.id, provider="paystack", account_number="9990001112",
        account_name="Jane Doe", bank_name="Wema Bank", currency="NGN", status="active",
    )
    db.add(account)
    db.commit()
    db.close()

    payload = {
        "event": "charge.success",
        "data": {"reference": "ref-xyz-001", "amount": 32000000, "account_number": "9990001112"},
    }
    raw = json.dumps(payload).encode()
    sig = hmac.new(b"sk_test_dummy", raw, hashlib.sha512).hexdigest()

    r = client.post(
        "/payments/webhook/paystack", data=raw,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "processed"

    # Redelivery of the same event must be a no-op (idempotency).
    r2 = client.post(
        "/payments/webhook/paystack", data=raw,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert r2.json()["status"] == "duplicate"


def test_payment_webhook_rejects_bad_signature():
    payload = {"event": "charge.success", "data": {"reference": "ref-bad", "amount": 1000}}
    raw = json.dumps(payload).encode()
    r = client.post(
        "/payments/webhook/paystack", data=raw,
        headers={"x-paystack-signature": "not-a-real-signature", "Content-Type": "application/json"},
    )
    assert r.status_code == 401


def test_create_virtual_account_requires_a_phone_number():
    client.post("/auth/register", json={"email": "nophone@test.com", "password": "Passw0rd!"})
    r = client.post("/auth/login", json={"email": "nophone@test.com", "password": "Passw0rd!"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.post("/payments/accounts", json={}, headers=headers)
    assert r.status_code == 400
    assert "phone" in r.json()["detail"].lower()


def _signed_webhook_post(payload: dict):
    raw = json.dumps(payload).encode()
    sig = hmac.new(b"sk_test_dummy", raw, hashlib.sha512).hexdigest()
    return client.post(
        "/payments/webhook/paystack", data=raw,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )


def test_dedicated_account_assignment_starts_pending_and_activates_via_webhook(monkeypatch):
    monkeypatch.setattr(
        "app.payments.assign_dedicated_account",
        lambda **kwargs: {"status": True, "message": "Assign dedicated account in progress"},
    )

    client.post("/auth/register", json={
        "email": "pending-user@test.com", "password": "Passw0rd!", "phone": "+2348100000001",
    })
    r = client.post("/auth/login", json={"email": "pending-user@test.com", "password": "Passw0rd!"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.post("/payments/accounts", json={}, headers=headers)
    assert r.status_code == 201
    assert r.json()["status"] == "pending"
    assert r.json()["account_number"] is None

    # A second request while still pending must not create a duplicate account.
    r2 = client.post("/payments/accounts", json={}, headers=headers)
    assert r2.json()["id"] == r.json()["id"]

    # Paystack confirms the DVA asynchronously.
    r3 = _signed_webhook_post({
        "event": "dedicatedaccount.assign.success",
        "data": {
            "email": "pending-user@test.com",
            "customer": {"customer_code": "CUS_abc123", "email": "pending-user@test.com"},
            "dedicated_account": {
                "account_number": "1122334455", "account_name": "Silo/Pending User",
                "bank": {"name": "Wema Bank"},
            },
        },
    })
    assert r3.json()["status"] == "activated"

    r4 = client.get("/payments/accounts", headers=headers)
    active = [a for a in r4.json() if a["status"] == "active"]
    assert len(active) == 1
    assert active[0]["account_number"] == "1122334455"
    assert active[0]["bank_name"] == "Wema Bank"


def test_dedicated_account_assignment_failure_is_recorded(monkeypatch):
    monkeypatch.setattr(
        "app.payments.assign_dedicated_account",
        lambda **kwargs: {"status": True, "message": "Assign dedicated account in progress"},
    )

    client.post("/auth/register", json={
        "email": "failed-user@test.com", "password": "Passw0rd!", "phone": "+2348100000002",
    })
    r = client.post("/auth/login", json={"email": "failed-user@test.com", "password": "Passw0rd!"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    client.post("/payments/accounts", json={}, headers=headers)

    r2 = _signed_webhook_post({
        "event": "customeridentification.failed",
        "data": {"email": "failed-user@test.com", "reason": "Account number or BVN is incorrect"},
    })
    assert r2.json()["status"] == "recorded_failure"

    r3 = client.get("/payments/accounts", headers=headers)
    assert r3.json()[0]["status"] == "failed"
    assert "BVN" in r3.json()[0]["failure_reason"]
