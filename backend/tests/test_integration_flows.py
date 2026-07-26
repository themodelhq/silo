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
    assert names == {"Basic Salary", "Rent", "Clothing", "Health"}

    r = client.get("/envelopes/", headers=auth_headers)
    envelope_names = {e["name"] for e in r.json()}
    assert {"Basic Salary", "Rent", "Clothing", "Health"}.issubset(envelope_names)

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


# ---- Outbound transfers ("send to any account") --------------------------

def _make_user_with_funded_envelope(email, balance=50000.0):
    client.post("/auth/register", json={"email": email, "password": "Passw0rd!", "phone": "+2348100000099"})
    r = client.post("/auth/login", json={"email": email, "password": "Passw0rd!"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.post("/envelopes/", json={"name": "Spending", "color": "#6366F1", "priority": 1}, headers=headers)
    envelope_id = r.json()["id"]

    db = _TestingSessionLocal()
    env = db.query(models.Envelope).filter(models.Envelope.id == envelope_id).first()
    env.balance = balance
    db.commit()
    db.close()

    return headers, envelope_id


def test_list_banks_proxies_provider(monkeypatch):
    monkeypatch.setattr("app.payments.list_banks", lambda **kwargs: [{"name": "Wema Bank", "code": "035"}])
    headers, _ = _make_user_with_funded_envelope("banks-user@test.com")
    r = client.get("/payments/banks", headers=headers)
    assert r.status_code == 200
    assert r.json() == [{"name": "Wema Bank", "code": "035"}]


def test_resolve_account_proxies_provider(monkeypatch):
    monkeypatch.setattr(
        "app.payments.resolve_account",
        lambda account_number, bank_code: {"account_number": account_number, "account_name": "JOHN A DOE"},
    )
    headers, _ = _make_user_with_funded_envelope("resolve-user@test.com")
    r = client.post("/payments/resolve-account", json={"account_number": "0022728151", "bank_code": "035"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["account_name"] == "JOHN A DOE"


def test_transfer_debits_envelope_and_creates_pending_transfer(monkeypatch):
    monkeypatch.setattr("app.payments.list_banks", lambda **kwargs: [{"name": "Wema Bank", "code": "035"}])
    monkeypatch.setattr("app.payments.create_transfer_recipient", lambda *a, **k: "RCP_test123")
    monkeypatch.setattr("app.payments.initiate_transfer", lambda *a, **k: {"status": "pending", "transfer_code": "TRF_test123"})

    headers, envelope_id = _make_user_with_funded_envelope("transfer-user@test.com", balance=50000.0)

    r = client.post("/payments/transfers", json={
        "envelope_id": envelope_id, "account_number": "0022728151", "bank_code": "035",
        "account_name": "JOHN A DOE", "amount": 15000.0, "reason": "Rent top-up",
    }, headers=headers)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    assert body["recipient_bank_name"] == "Wema Bank"

    r2 = client.get("/envelopes/", headers=headers)
    envelope = next(e for e in r2.json() if e["id"] == envelope_id)
    assert envelope["balance"] == 35000.0, "envelope should be debited immediately (optimistic)"


def test_transfer_rejects_insufficient_balance(monkeypatch):
    monkeypatch.setattr("app.payments.create_transfer_recipient", lambda *a, **k: "RCP_x")
    monkeypatch.setattr("app.payments.initiate_transfer", lambda *a, **k: {"status": "pending", "transfer_code": "TRF_x"})
    headers, envelope_id = _make_user_with_funded_envelope("poor-user@test.com", balance=1000.0)

    r = client.post("/payments/transfers", json={
        "envelope_id": envelope_id, "account_number": "0022728151", "bank_code": "035", "amount": 5000.0,
    }, headers=headers)
    assert r.status_code == 400
    assert "insufficient" in r.json()["detail"].lower()


def test_transfer_rejects_locked_envelope(monkeypatch):
    headers, envelope_id = _make_user_with_funded_envelope("locked-user@test.com", balance=50000.0)
    client_lock = client.post(f"/envelopes/{envelope_id}/lock", headers=headers)
    assert client_lock.status_code == 200

    r = client.post("/payments/transfers", json={
        "envelope_id": envelope_id, "account_number": "0022728151", "bank_code": "035", "amount": 5000.0,
    }, headers=headers)
    assert r.status_code == 400
    assert "locked" in r.json()["detail"].lower()


def test_transfer_success_webhook_marks_success_without_refund(monkeypatch):
    monkeypatch.setattr("app.payments.list_banks", lambda **kwargs: [])
    monkeypatch.setattr("app.payments.create_transfer_recipient", lambda *a, **k: "RCP_ok")
    monkeypatch.setattr("app.payments.initiate_transfer", lambda *a, **k: {"status": "pending", "transfer_code": "TRF_ok"})

    headers, envelope_id = _make_user_with_funded_envelope("success-user@test.com", balance=50000.0)
    r = client.post("/payments/transfers", json={
        "envelope_id": envelope_id, "account_number": "1111111111", "bank_code": "058", "amount": 10000.0,
    }, headers=headers)
    reference = r.json()["id"]  # not the provider reference — fetch it from DB instead
    db = _TestingSessionLocal()
    transfer = db.query(models.PayoutTransfer).filter(models.PayoutTransfer.id == reference).first()
    provider_reference = transfer.provider_reference
    db.close()

    r2 = _signed_webhook_post({"event": "transfer.success", "data": {"reference": provider_reference}})
    assert r2.json()["status"] == "recorded_success"

    r3 = client.get("/envelopes/", headers=headers)
    envelope = next(e for e in r3.json() if e["id"] == envelope_id)
    assert envelope["balance"] == 40000.0, "a successful transfer should NOT be refunded"


def test_transfer_failed_webhook_refunds_envelope(monkeypatch):
    monkeypatch.setattr("app.payments.list_banks", lambda **kwargs: [])
    monkeypatch.setattr("app.payments.create_transfer_recipient", lambda *a, **k: "RCP_fail")
    monkeypatch.setattr("app.payments.initiate_transfer", lambda *a, **k: {"status": "pending", "transfer_code": "TRF_fail"})

    headers, envelope_id = _make_user_with_funded_envelope("refund-user@test.com", balance=50000.0)
    r = client.post("/payments/transfers", json={
        "envelope_id": envelope_id, "account_number": "2222222222", "bank_code": "058", "amount": 12000.0,
    }, headers=headers)
    transfer_id = r.json()["id"]
    db = _TestingSessionLocal()
    transfer = db.query(models.PayoutTransfer).filter(models.PayoutTransfer.id == transfer_id).first()
    provider_reference = transfer.provider_reference
    db.close()

    # Envelope debited immediately.
    r_mid = client.get("/envelopes/", headers=headers)
    assert next(e for e in r_mid.json() if e["id"] == envelope_id)["balance"] == 38000.0

    r2 = _signed_webhook_post({
        "event": "transfer.failed",
        "data": {"reference": provider_reference, "reason": "Insufficient balance in Paystack account"},
    })
    assert r2.json()["status"] == "recorded_failure_and_refunded"

    r3 = client.get("/envelopes/", headers=headers)
    envelope = next(e for e in r3.json() if e["id"] == envelope_id)
    assert envelope["balance"] == 50000.0, "a failed transfer should be refunded back to the envelope"

    r4 = client.get("/payments/transfers", headers=headers)
    record = next(t for t in r4.json() if t["id"] == transfer_id)
    assert record["status"] == "failed"
    assert "Insufficient balance" in record["failure_reason"]
