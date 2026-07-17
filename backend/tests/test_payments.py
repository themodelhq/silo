import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

from app.payments import split_amount_by_rules, verify_webhook_signature


def _rule(name, allocation_type, value, priority):
    return SimpleNamespace(envelope_name=name, allocation_type=allocation_type, value=value, priority=priority)


def test_percentage_split_sums_to_total_when_fully_allocated():
    rules = [
        _rule("Rent", "PERCENTAGE", 0.5, 1),
        _rule("Clothing", "PERCENTAGE", 0.2, 2),
        _rule("Health", "PERCENTAGE", 0.3, 3),
    ]
    result = split_amount_by_rules(rules, 100000)
    total = sum(portion for _, portion in result)
    assert total == 100000
    assert dict((r.envelope_name, p) for r, p in result) == {"Rent": 50000, "Clothing": 20000, "Health": 30000}


def test_fixed_rule_then_percentage_remainder():
    rules = [
        _rule("Savings", "FIXED", 20000, 1),
        _rule("Everything Else", "REMAINDER", 0, 2),
    ]
    result = split_amount_by_rules(rules, 100000)
    breakdown = dict((r.envelope_name, p) for r, p in result)
    assert breakdown["Savings"] == 20000
    assert breakdown["Everything Else"] == 80000


def test_leftover_without_remainder_rule_folds_into_last_rule():
    """If percentages don't add up to 100% and there's no REMAINDER rule,
    money must never silently disappear — it should land somewhere visible."""
    rules = [
        _rule("Rent", "PERCENTAGE", 0.3, 1),
        _rule("Clothing", "PERCENTAGE", 0.1, 2),
    ]
    result = split_amount_by_rules(rules, 100000)
    total = sum(portion for _, portion in result)
    assert total == 100000


def test_no_rules_returns_empty():
    assert split_amount_by_rules([], 50000) == []


def test_negative_amount_rejected():
    import pytest
    with pytest.raises(ValueError):
        split_amount_by_rules([_rule("Rent", "PERCENTAGE", 1.0, 1)], -5)


def test_webhook_signature_verification():
    import os as _os
    _os.environ["PAYSTACK_SECRET_KEY"] = "sk_test_dummy"
    import importlib
    from app import payments as payments_module
    importlib.reload(payments_module)

    import hmac, hashlib
    body = b'{"event": "charge.success"}'
    good_sig = hmac.new(b"sk_test_dummy", body, hashlib.sha512).hexdigest()

    assert payments_module.verify_webhook_signature(body, good_sig) is True
    assert payments_module.verify_webhook_signature(body, "wrong-signature") is False
    assert payments_module.verify_webhook_signature(body, None) is False
