"""
Silo Payment Integration
================================
Generates a dedicated/virtual bank account number for a user through a
payment provider (Paystack by default — already referenced as an intended
provider by `models.Subscription.provider`), and deterministically splits any
money received into that account across the user's active envelope rules.
It also supports the reverse direction: sending money out of an envelope to
any bank account the user specifies (`list_banks` / `resolve_account` /
`create_transfer_recipient` / `initiate_transfer`), which is what powers the
"Account" option in the envelope Transfer To dropdown.

No AI or inference anywhere: account creation is a direct provider API call,
and the split on receipt is the same percentage/fixed/remainder arithmetic
used everywhere else in this codebase (see `envelope_engine.py`).

Design
------
- `assign_dedicated_account` is the primary DVA creation path: Paystack's
  single-step `/dedicated_account/assign`, which creates the customer,
  validates their identity (BVN) if your Paystack business category
  requires it, and creates the account — all as one asynchronous request.
  It only confirms the request was accepted; the account number itself
  arrives later via webhook (`dedicatedaccount.assign.success/.failed`,
  and `customeridentification.success/.failed` if validation ran).
- `create_customer` / `create_dedicated_virtual_account` / `validate_customer`
  are the multi-step equivalents, kept for callers who want to control each
  step explicitly (e.g. re-validating a customer later).
- `verify_webhook_signature` implements Paystack's documented HMAC-SHA512
  webhook verification so `/payments/webhook/paystack` can trust inbound
  events.
- `split_amount_by_rules` is a pure function (no DB/network) that mirrors
  `envelope_engine.EnvelopeEngine.allocate`'s priority/percentage/fixed/
  remainder semantics, but is *additive* (top-up) rather than a reset —
  appropriate for a real inbound payment landing on top of existing balances.

Swapping providers (Flutterwave, Stripe, etc.) means adding sibling functions
here with the same signatures — nothing above this module needs to change.
"""

from __future__ import annotations

import hmac
import hashlib
import os
from typing import Optional

import httpx

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_BASE_URL = "https://api.paystack.co"
# Paystack requires a "preferred bank" partner for dedicated virtual accounts.
# Wema Bank and Titan-Paystack are the two most commonly enabled for NGN DVAs.
DEFAULT_PREFERRED_BANK = os.getenv("PAYSTACK_PREFERRED_BANK", "wema-bank")

_HTTP_TIMEOUT = 15.0


class PaymentProviderError(Exception):
    """Raised for any provider configuration or API-level failure."""


def _require_secret_key() -> str:
    if not PAYSTACK_SECRET_KEY:
        raise PaymentProviderError(
            "PAYSTACK_SECRET_KEY is not configured. Set it as an environment "
            "variable (see backend/README.md) to enable virtual account creation."
        )
    return PAYSTACK_SECRET_KEY


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_require_secret_key()}",
        "Content-Type": "application/json",
    }


def _split_name(full_name: Optional[str]) -> tuple[str, str]:
    if not full_name or not full_name.strip():
        return "Silo", "User"
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], "User"
    return parts[0], " ".join(parts[1:])


def create_customer(email: str, full_name: Optional[str] = None, phone: Optional[str] = None) -> str:
    """Create (or the provider may de-dupe) a Paystack customer for this user
    and return their `customer_code`. Kept for the multi-step integration
    flow / other Paystack features; the primary DVA path below
    (`assign_dedicated_account`) creates the customer implicitly."""
    first_name, last_name = _split_name(full_name)
    body = {"email": email, "first_name": first_name, "last_name": last_name}
    if phone:
        body["phone"] = phone
    try:
        resp = httpx.post(f"{PAYSTACK_BASE_URL}/customer", headers=_headers(), json=body, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise PaymentProviderError(f"Could not reach Paystack: {exc}") from exc

    payload = resp.json()
    if resp.status_code >= 400 or not payload.get("status", False):
        raise PaymentProviderError(payload.get("message", "Failed to create Paystack customer."))
    return payload["data"]["customer_code"]


def create_dedicated_virtual_account(
    customer_code: str, preferred_bank: str = DEFAULT_PREFERRED_BANK
) -> dict:
    """Multi-step flow, second call: create a dedicated virtual (NUBAN)
    account for an already-created (and, if required, already-validated)
    customer. Only use this directly for businesses in the "optional
    compliance" category (see Paystack docs) — for Betting/Financial
    Services/General Services businesses, use `assign_dedicated_account`
    instead, which handles customer creation + validation + DVA assignment
    as a single, correctly-ordered request."""
    try:
        resp = httpx.post(
            f"{PAYSTACK_BASE_URL}/dedicated_account",
            headers=_headers(),
            json={"customer": customer_code, "preferred_bank": preferred_bank},
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PaymentProviderError(f"Could not reach Paystack: {exc}") from exc

    payload = resp.json()
    if resp.status_code >= 400 or not payload.get("status", False):
        raise PaymentProviderError(payload.get("message", "Failed to create a dedicated virtual account."))

    data = payload["data"]
    bank = data.get("bank", {}) or {}
    return {
        "account_number": data.get("account_number"),
        "account_name": data.get("account_name"),
        "bank_name": bank.get("name"),
        "reference": data.get("id"),
    }


def validate_customer(
    customer_code: str, bvn: str, bank_code: str, account_number: str,
    first_name: str, last_name: str, country: str = "NG",
) -> None:
    """Multi-step flow: explicitly validate a customer's identity ahead of
    creating their DVA. This is asynchronous — Paystack returns "in progress"
    immediately and later fires `customeridentification.success` or
    `.failed` to the webhook. Not needed on the primary single-step path
    (`assign_dedicated_account` performs this internally), but kept for
    callers who want to run the multi-step flow explicitly, or re-validate
    a customer."""
    body = {
        "country": country, "type": "bank_account", "account_number": account_number,
        "bvn": bvn, "bank_code": bank_code, "first_name": first_name, "last_name": last_name,
    }
    try:
        resp = httpx.post(
            f"{PAYSTACK_BASE_URL}/customer/{customer_code}/identification",
            headers=_headers(), json=body, timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PaymentProviderError(f"Could not reach Paystack: {exc}") from exc

    payload = resp.json()
    if resp.status_code >= 400 or not payload.get("status", False):
        raise PaymentProviderError(payload.get("message", "Failed to submit customer validation."))


def assign_dedicated_account(
    email: str,
    full_name: Optional[str],
    phone: str,
    preferred_bank: str = DEFAULT_PREFERRED_BANK,
    country: str = "NG",
    bvn: Optional[str] = None,
    bank_code: Optional[str] = None,
    account_number: Optional[str] = None,
) -> dict:
    """Primary DVA creation path: Paystack's single-step
    `POST /dedicated_account/assign`, which creates the customer, validates
    their identity if required, and creates the DVA — all in one request.

    Pass `bvn` + `bank_code` + `account_number` for the "required compliance"
    path (Betting / Financial Services / General Services business
    categories); omit all three for the "optional compliance" path. Either
    way, this call only confirms the request was *accepted* — the actual
    account number is not returned here. It arrives later via the
    `dedicatedaccount.assign.success` webhook event (or a `.failed` /
    `customeridentification.failed` event if something goes wrong).

    Raises `PaymentProviderError` if the request itself is rejected (bad
    phone format, missing required fields, business not go-live, etc).
    """
    if not phone:
        raise PaymentProviderError("A phone number is required by Paystack to create a dedicated virtual account.")

    first_name, last_name = _split_name(full_name)
    payload = {
        "email": email, "first_name": first_name, "last_name": last_name,
        "phone": phone, "preferred_bank": preferred_bank, "country": country,
    }
    if bvn and bank_code and account_number:
        payload.update({"bvn": bvn, "bank_code": bank_code, "account_number": account_number})

    try:
        resp = httpx.post(
            f"{PAYSTACK_BASE_URL}/dedicated_account/assign",
            headers=_headers(), json=payload, timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PaymentProviderError(f"Could not reach Paystack: {exc}") from exc

    data = resp.json()
    if resp.status_code >= 400 or not data.get("status", False):
        raise PaymentProviderError(data.get("message", "Failed to request a dedicated virtual account."))
    return data  # {"status": True, "message": "Assign dedicated account in progress"}


def verify_webhook_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """Verify Paystack's `X-Paystack-Signature` header: HMAC-SHA512 of the raw
    request body, keyed with the account's secret key."""
    if not PAYSTACK_SECRET_KEY or not signature:
        return False
    computed = hmac.new(PAYSTACK_SECRET_KEY.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature)


def split_amount_by_rules(rules: list, amount: float) -> list[tuple]:
    """Deterministically split `amount` across `rules` (objects/rows exposing
    `.allocation_type`, `.value`, `.priority`, `.envelope_name`), honouring the
    same PERCENTAGE / FIXED / REMAINDER semantics as the envelope engine.

    Unlike `EnvelopeEngine.allocate` (which sets absolute allocated/balance —
    appropriate for "here is my whole salary"), this is meant to be added on
    top of existing envelope balances for a single inbound payment, so it just
    returns `[(rule, portion), ...]` for the caller to apply.

    If no REMAINDER rule exists and money is left over after every
    PERCENTAGE/FIXED rule runs, the leftover is folded into the last rule
    (by priority) rather than silently dropped.
    """
    if amount < 0:
        raise ValueError("amount cannot be negative.")
    if not rules:
        return []

    ordered = sorted(rules, key=lambda r: r.priority)
    remaining = amount
    result: list[tuple] = []
    remainder_rule = None

    for rule in ordered:
        if rule.allocation_type == "REMAINDER":
            if remainder_rule is None:
                remainder_rule = rule
            continue

        portion = amount * rule.value if rule.allocation_type == "PERCENTAGE" else rule.value
        portion = round(min(portion, max(remaining, 0.0)), 2)
        remaining = round(remaining - portion, 2)
        result.append((rule, portion))

    if remainder_rule is not None:
        result.append((remainder_rule, round(max(remaining, 0.0), 2)))
    elif remaining > 0 and result:
        last_rule, last_amount = result[-1]
        result[-1] = (last_rule, round(last_amount + remaining, 2))
    elif remaining > 0:
        # No rules could absorb anything (e.g. all zero-value) — return the
        # full amount against the first rule so nothing is silently lost.
        result.append((ordered[0], round(remaining, 2)))

    return result


# --------------------------------------------------------------------------
# Outbound transfers ("send to any account") — the "Account" option in the
# envelope Transfer To dropdown. Uses Paystack's Transfers API: resolve the
# destination account, create a reusable transfer recipient, then initiate
# the transfer. See https://paystack.com/docs/transfers/ for the full
# lifecycle — summarized in PayoutTransfer's docstring in models.py.
#
# Two real-world prerequisites this code can't satisfy on its own:
#   1. The Paystack account needs an actual funded balance to send from.
#   2. By default Paystack requires OTP confirmation per transfer; automated
#      flows like this one need "Confirm transfers before sending" turned
#      OFF in Dashboard → Settings → Preferences. If it's left on, Paystack
#      returns status "otp" instead of completing the transfer, which this
#      code surfaces as-is rather than pretending it succeeded.
# --------------------------------------------------------------------------

def list_banks(country: str = "nigeria", currency: str = "NGN") -> list[dict]:
    try:
        resp = httpx.get(
            f"{PAYSTACK_BASE_URL}/bank",
            headers=_headers(), params={"country": country, "currency": currency, "perPage": 100},
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PaymentProviderError(f"Could not reach Paystack: {exc}") from exc

    payload = resp.json()
    if resp.status_code >= 400 or not payload.get("status", False):
        raise PaymentProviderError(payload.get("message", "Failed to fetch the bank list."))
    return [{"name": b.get("name"), "code": b.get("code")} for b in payload.get("data", [])]


def resolve_account(account_number: str, bank_code: str) -> dict:
    """Confirms a destination account is real and returns the name on it,
    so the person can double-check before sending money — same idea as the
    "confirm recipient" step every Nigerian banking app shows."""
    try:
        resp = httpx.get(
            f"{PAYSTACK_BASE_URL}/bank/resolve",
            headers=_headers(), params={"account_number": account_number, "bank_code": bank_code},
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PaymentProviderError(f"Could not reach Paystack: {exc}") from exc

    payload = resp.json()
    if resp.status_code >= 400 or not payload.get("status", False):
        raise PaymentProviderError(payload.get("message", "Could not verify that account number."))
    data = payload["data"]
    return {"account_number": data.get("account_number"), "account_name": data.get("account_name")}


def create_transfer_recipient(name: str, account_number: str, bank_code: str, currency: str = "NGN") -> str:
    """Returns a recipient_code. Paystack de-dupes on account number, so
    calling this again for the same account is safe and returns the same
    recipient rather than erroring."""
    try:
        resp = httpx.post(
            f"{PAYSTACK_BASE_URL}/transferrecipient",
            headers=_headers(),
            json={"type": "nuban", "name": name, "account_number": account_number, "bank_code": bank_code, "currency": currency},
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PaymentProviderError(f"Could not reach Paystack: {exc}") from exc

    payload = resp.json()
    if resp.status_code >= 400 or not payload.get("status", False):
        raise PaymentProviderError(payload.get("message", "Failed to register that recipient with Paystack."))
    return payload["data"]["recipient_code"]


def initiate_transfer(amount: float, recipient_code: str, reference: str, reason: str = "") -> dict:
    """Amount is in the currency's major unit (Naira); converted to kobo
    here. Returns {"status": "pending"|"otp"|..., "transfer_code": ...}."""
    try:
        resp = httpx.post(
            f"{PAYSTACK_BASE_URL}/transfer",
            headers=_headers(),
            json={
                "source": "balance", "amount": round(amount * 100), "recipient": recipient_code,
                "reference": reference, "reason": reason or "Silo envelope transfer",
            },
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PaymentProviderError(f"Could not reach Paystack: {exc}") from exc

    payload = resp.json()
    if resp.status_code >= 400 or not payload.get("status", False):
        raise PaymentProviderError(payload.get("message", "Failed to initiate the transfer."))
    data = payload["data"]
    return {"status": data.get("status"), "transfer_code": data.get("transfer_code")}
