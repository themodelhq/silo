"""
PayEnvelope Payment Integration
================================
Generates a dedicated/virtual bank account number for a user through a
payment provider (Paystack by default — already referenced as an intended
provider by `models.Subscription.provider`), and deterministically splits any
money received into that account across the user's active envelope rules.

No AI or inference anywhere: account creation is a direct provider API call,
and the split on receipt is the same percentage/fixed/remainder arithmetic
used everywhere else in this codebase (see `envelope_engine.py`).

Design
------
- `create_customer` / `create_dedicated_virtual_account` wrap the Paystack
  REST API. They require `PAYSTACK_SECRET_KEY` to be set; if it isn't, they
  raise `PaymentProviderError` with a clear message rather than silently
  failing, so the API layer can return a helpful 502.
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
        return "PayEnvelope", "User"
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], "User"
    return parts[0], " ".join(parts[1:])


def create_customer(email: str, full_name: Optional[str] = None) -> str:
    """Create (or the provider may de-dupe) a Paystack customer for this user
    and return their `customer_code`."""
    first_name, last_name = _split_name(full_name)
    try:
        resp = httpx.post(
            f"{PAYSTACK_BASE_URL}/customer",
            headers=_headers(),
            json={"email": email, "first_name": first_name, "last_name": last_name},
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PaymentProviderError(f"Could not reach Paystack: {exc}") from exc

    payload = resp.json()
    if resp.status_code >= 400 or not payload.get("status", False):
        raise PaymentProviderError(payload.get("message", "Failed to create Paystack customer."))
    return payload["data"]["customer_code"]


def create_dedicated_virtual_account(
    customer_code: str, preferred_bank: str = DEFAULT_PREFERRED_BANK
) -> dict:
    """Create a dedicated virtual (NUBAN) account for the given customer.
    Returns {"account_number", "account_name", "bank_name", "reference"}."""
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
