"""
PayEnvelope Envelope Engine
============================
Deterministic, rule-based budget allocation. No ML, no AI — every allocation
decision traces back to a percentage, a fixed amount, or a "remainder" rule
that a user configured (or a documented Nigeria-first default).

Core concepts
-------------
Envelope        A named bucket of money with a balance, a color, a priority,
                and optional lock/archive/recurring flags.
AllocationRule  Describes how a slice of net salary is assigned to an
                envelope: a fixed percentage, a fixed amount, or "remainder"
                (whatever's left after all percentage/fixed rules run).
EnvelopeEngine  Applies an ordered list of AllocationRules against a net
                salary figure to produce envelope balances, and provides the
                envelope lifecycle operations (create/rename/archive/merge/
                split/lock) requested in the product brief.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AllocationType(str, Enum):
    PERCENTAGE = "PERCENTAGE"   # e.g. 0.30 of net salary
    FIXED = "FIXED"             # e.g. a flat amount regardless of salary size
    REMAINDER = "REMAINDER"     # whatever is left after all other rules run


# Nigeria-first default rule set, mirroring the brief's example allocation.
# Percentages sum to 65%; the remaining 35% is split between an explicit
# emergency-fund percentage and a REMAINDER envelope for discretionary spend.
DEFAULT_ALLOCATION_RULES: list[dict] = [
    {"envelope_name": "Rent",            "type": AllocationType.PERCENTAGE, "value": 0.30, "color": "#6366F1", "priority": 1},
    {"envelope_name": "Transportation",  "type": AllocationType.PERCENTAGE, "value": 0.15, "color": "#38BDF8", "priority": 2},
    {"envelope_name": "Savings",         "type": AllocationType.PERCENTAGE, "value": 0.10, "color": "#10B981", "priority": 3},
    {"envelope_name": "Utilities",       "type": AllocationType.PERCENTAGE, "value": 0.05, "color": "#F59E0B", "priority": 4},
    {"envelope_name": "Emergency Fund",  "type": AllocationType.PERCENTAGE, "value": 0.05, "color": "#EF4444", "priority": 5},
    {"envelope_name": "Discretionary Spending", "type": AllocationType.REMAINDER, "value": 0.0, "color": "#8B5CF6", "priority": 6},
]

DEFAULT_ENVELOPE_CATALOG = [
    "Rent", "Food", "Transportation", "Fuel", "Utilities", "Internet",
    "Electricity", "Water", "DSTV", "Savings", "Emergency Fund", "Health",
    "Insurance", "Education", "Parents", "Giving", "Entertainment",
    "Shopping", "Travel", "Miscellaneous",
]


@dataclass
class AllocationRule:
    envelope_name: str
    type: AllocationType
    value: float  # fraction (0-1) for PERCENTAGE, absolute amount for FIXED, ignored for REMAINDER
    color: str = "#6366F1"
    priority: int = 99
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def validate(self) -> None:
        if self.type == AllocationType.PERCENTAGE and not (0 <= self.value <= 1):
            raise ValueError(f"Percentage rule for '{self.envelope_name}' must be between 0 and 1, got {self.value}")
        if self.type == AllocationType.FIXED and self.value < 0:
            raise ValueError(f"Fixed rule for '{self.envelope_name}' cannot be negative")


@dataclass
class Envelope:
    name: str
    balance: float = 0.0
    allocated: float = 0.0
    color: str = "#6366F1"
    priority: int = 99
    locked: bool = False
    archived: bool = False
    recurring: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def spend(self, amount: float) -> None:
        if self.locked:
            raise PermissionError(f"Envelope '{self.name}' is locked and cannot be spent from.")
        if amount < 0:
            raise ValueError("Spend amount must be non-negative.")
        self.balance = round(self.balance - amount, 2)

    def deposit(self, amount: float) -> None:
        if amount < 0:
            raise ValueError("Deposit amount must be non-negative.")
        self.balance = round(self.balance + amount, 2)


def validate_rule_set(rules: list[AllocationRule]) -> list[str]:
    """Return a list of human-readable warnings (empty list = clean)."""
    warnings: list[str] = []
    for r in rules:
        r.validate()

    pct_total = sum(r.value for r in rules if r.type == AllocationType.PERCENTAGE)
    remainder_rules = [r for r in rules if r.type == AllocationType.REMAINDER]

    if pct_total > 1.0:
        warnings.append(f"Percentage rules sum to {pct_total * 100:.1f}%, which exceeds 100%.")
    if len(remainder_rules) > 1:
        warnings.append("More than one REMAINDER rule is defined; only the first will receive funds.")
    return warnings


class EnvelopeEngine:
    """Applies AllocationRules to a net salary figure and manages envelope
    lifecycle operations (create, rename, delete, archive, merge, split, lock)."""

    def __init__(self, rules: Optional[list[AllocationRule]] = None):
        self.rules: list[AllocationRule] = rules or [AllocationRule(**r) for r in DEFAULT_ALLOCATION_RULES]
        self.envelopes: dict[str, Envelope] = {}

    # -- Allocation ---------------------------------------------------

    def allocate(self, net_salary: float) -> dict[str, Envelope]:
        """Distribute `net_salary` across envelopes per the active rule set.
        Fixed and percentage rules are applied first (sorted by priority),
        then a single REMAINDER rule (if any) absorbs whatever is left."""
        if net_salary < 0:
            raise ValueError("net_salary cannot be negative.")

        warnings = validate_rule_set(self.rules)  # noqa: F841 (surfaced via .last_warnings if needed)
        self.last_warnings = warnings

        ordered = sorted(self.rules, key=lambda r: r.priority)
        remaining = net_salary
        remainder_rule: Optional[AllocationRule] = None

        for rule in ordered:
            if rule.type == AllocationType.REMAINDER:
                if remainder_rule is None:
                    remainder_rule = rule
                continue

            amount = (net_salary * rule.value) if rule.type == AllocationType.PERCENTAGE else rule.value
            amount = round(min(amount, max(remaining, 0.0)), 2)
            remaining = round(remaining - amount, 2)
            self._upsert_envelope(rule.envelope_name, amount, rule.color, rule.priority)

        if remainder_rule is not None:
            amount = round(max(remaining, 0.0), 2)
            self._upsert_envelope(remainder_rule.envelope_name, amount, remainder_rule.color, remainder_rule.priority)

        return self.envelopes

    def _upsert_envelope(self, name: str, amount: float, color: str, priority: int) -> None:
        env = self.envelopes.get(name)
        if env is None:
            env = Envelope(name=name, color=color, priority=priority)
            self.envelopes[name] = env
        env.allocated = amount
        env.balance = amount

    # -- Lifecycle operations ------------------------------------------

    def create_envelope(self, name: str, color: str = "#6366F1", priority: int = 99) -> Envelope:
        if name in self.envelopes:
            raise ValueError(f"Envelope '{name}' already exists.")
        env = Envelope(name=name, color=color, priority=priority)
        self.envelopes[name] = env
        return env

    def rename_envelope(self, old_name: str, new_name: str) -> None:
        env = self._require(old_name)
        env.name = new_name
        self.envelopes[new_name] = self.envelopes.pop(old_name)

    def delete_envelope(self, name: str) -> None:
        env = self._require(name)
        if env.locked:
            raise PermissionError(f"Envelope '{name}' is locked; unlock before deleting.")
        del self.envelopes[name]

    def archive_envelope(self, name: str) -> None:
        self._require(name).archived = True

    def unarchive_envelope(self, name: str) -> None:
        self._require(name).archived = False

    def lock_envelope(self, name: str) -> None:
        self._require(name).locked = True

    def unlock_envelope(self, name: str) -> None:
        self._require(name).locked = False

    def merge_envelopes(self, source_name: str, target_name: str) -> Envelope:
        source = self._require(source_name)
        target = self._require(target_name)
        target.balance = round(target.balance + source.balance, 2)
        target.allocated = round(target.allocated + source.allocated, 2)
        del self.envelopes[source_name]
        return target

    def split_envelope(self, name: str, new_name: str, fraction: float) -> tuple[Envelope, Envelope]:
        if not (0 < fraction < 1):
            raise ValueError("fraction must be strictly between 0 and 1.")
        source = self._require(name)
        moved_balance = round(source.balance * fraction, 2)
        moved_allocated = round(source.allocated * fraction, 2)
        source.balance = round(source.balance - moved_balance, 2)
        source.allocated = round(source.allocated - moved_allocated, 2)
        new_env = Envelope(name=new_name, balance=moved_balance, allocated=moved_allocated,
                            color=source.color, priority=source.priority)
        self.envelopes[new_name] = new_env
        return source, new_env

    def transfer(self, from_name: str, to_name: str, amount: float) -> None:
        source = self._require(from_name)
        target = self._require(to_name)
        source.spend(amount)
        target.deposit(amount)

    def _require(self, name: str) -> Envelope:
        if name not in self.envelopes:
            raise KeyError(f"No envelope named '{name}'.")
        return self.envelopes[name]
