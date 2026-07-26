import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.envelope_engine import EnvelopeEngine, AllocationRule, AllocationType, validate_rule_set


def test_default_allocation_sums_to_net_salary():
    engine = EnvelopeEngine()
    envelopes = engine.allocate(619000.00)
    total_allocated = sum(e.balance for e in envelopes.values())
    assert round(total_allocated, 2) == 619000.00


def test_percentage_rules_apply_correctly():
    engine = EnvelopeEngine()
    envelopes = engine.allocate(100000.00)
    assert envelopes["Rent"].balance == 30000.00
    assert envelopes["Transportation"].balance == 15000.00
    assert envelopes["Savings"].balance == 10000.00


def test_remainder_rule_absorbs_leftover():
    engine = EnvelopeEngine()
    envelopes = engine.allocate(100000.00)
    # 30 + 15 + 10 + 5 + 5 = 65% assigned, 35% remainder
    assert envelopes["Discretionary Spending"].balance == 35000.00


def test_custom_rules_override_defaults():
    rules = [
        AllocationRule(envelope_name="Rent", type=AllocationType.PERCENTAGE, value=0.5, priority=1),
        AllocationRule(envelope_name="Everything Else", type=AllocationType.REMAINDER, value=0, priority=2),
    ]
    engine = EnvelopeEngine(rules=rules)
    envelopes = engine.allocate(200000.00)
    assert envelopes["Rent"].balance == 100000.00
    assert envelopes["Everything Else"].balance == 100000.00


def test_fixed_allocation_rule():
    rules = [
        AllocationRule(envelope_name="Internet", type=AllocationType.FIXED, value=15000.00, priority=1),
        AllocationRule(envelope_name="Rest", type=AllocationType.REMAINDER, value=0, priority=2),
    ]
    engine = EnvelopeEngine(rules=rules)
    envelopes = engine.allocate(100000.00)
    assert envelopes["Internet"].balance == 15000.00
    assert envelopes["Rest"].balance == 85000.00


def test_invalid_percentage_raises():
    with pytest.raises(ValueError):
        AllocationRule(envelope_name="Bad", type=AllocationType.PERCENTAGE, value=1.5).validate()


def test_rule_set_warns_when_over_100_percent():
    rules = [
        AllocationRule(envelope_name="A", type=AllocationType.PERCENTAGE, value=0.7),
        AllocationRule(envelope_name="B", type=AllocationType.PERCENTAGE, value=0.5),
    ]
    warnings = validate_rule_set(rules)
    assert any("exceeds 100%" in w for w in warnings)


def test_lock_prevents_spend():
    engine = EnvelopeEngine()
    engine.create_envelope("Test")
    engine.lock_envelope("Test")
    with pytest.raises(PermissionError):
        engine.envelopes["Test"].spend(10.0)


def test_merge_envelopes_combines_balances():
    engine = EnvelopeEngine()
    engine.create_envelope("A")
    engine.create_envelope("B")
    engine.envelopes["A"].deposit(1000)
    engine.envelopes["B"].deposit(500)
    merged = engine.merge_envelopes("A", "B")
    assert merged.balance == 1500
    assert "A" not in engine.envelopes


def test_split_envelope_divides_balance():
    engine = EnvelopeEngine()
    engine.create_envelope("Food")
    engine.envelopes["Food"].deposit(10000)
    remaining, new_env = engine.split_envelope("Food", "Snacks", 0.2)
    assert new_env.balance == 2000
    assert remaining.balance == 8000


def test_transfer_between_envelopes():
    engine = EnvelopeEngine()
    engine.create_envelope("A")
    engine.create_envelope("B")
    engine.envelopes["A"].deposit(5000)
    engine.transfer("A", "B", 2000)
    assert engine.envelopes["A"].balance == 3000
    assert engine.envelopes["B"].balance == 2000
