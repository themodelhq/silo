"""
Silo Payslip Parser
===========================
A fully deterministic, rule-based payslip text parser.

No machine learning. No AI. Every extracted field comes from a documented
regular expression matched against normalized payslip text, followed by
transparent arithmetic (never inference) to fill in any missing totals.

Design goals:
- Predictable: the same input always produces the same output.
- Explainable: every field has a rule you can point to and a confidence flag.
- Configurable: country-specific deduction labels (PAYE, NHF, SSNIT, NSSF,
  UIF, etc.) are data, not code, so new countries are added by extending
  COUNTRY_PROFILES rather than touching the parsing engine.
- Nigeria-first: the default profile and sample corpus target Nigerian
  corporate/civil-service payslip formats, but the engine is generic enough
  to run against Ghanaian, Kenyan, or South African payslips by selecting a
  different profile (or auto-detecting one, see `detect_country`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------
# Currency / country configuration (data, not logic)
# --------------------------------------------------------------------------

class Country(str, Enum):
    NIGERIA = "NG"
    GHANA = "GH"
    KENYA = "KE"
    SOUTH_AFRICA = "ZA"
    UNKNOWN = "XX"


@dataclass(frozen=True)
class CountryProfile:
    country: Country
    currency_code: str
    currency_symbols: tuple  # e.g. ("₦", "NGN", "N")
    statutory_deduction_labels: dict  # canonical_field -> regex alternation fragment
    pension_default_rate: float  # used only as an allocation hint, never fabricated as fact
    month_names_locale: str = "en"


COUNTRY_PROFILES: dict[Country, CountryProfile] = {
    Country.NIGERIA: CountryProfile(
        country=Country.NIGERIA,
        currency_code="NGN",
        currency_symbols=("₦", "NGN", "naira"),
        statutory_deduction_labels={
            "tax": r"paye|pay\s*as\s*you\s*earn|income\s*tax|\btax\b",
            "pension": r"pension|pfa|rsa\s*contribution",
            "nhf": r"nhf|national\s*housing\s*fund",
        },
        pension_default_rate=0.08,
    ),
    Country.GHANA: CountryProfile(
        country=Country.GHANA,
        currency_code="GHS",
        currency_symbols=("GH₵", "GHS", "cedis"),
        statutory_deduction_labels={
            "tax": r"paye|income\s*tax|\btax\b",
            "pension": r"ssnit|pension|tier\s*[123]",
            "nhf": r"nhis",
        },
        pension_default_rate=0.055,
    ),
    Country.KENYA: CountryProfile(
        country=Country.KENYA,
        currency_code="KES",
        currency_symbols=("KSh", "KES", "shillings"),
        statutory_deduction_labels={
            "tax": r"paye|income\s*tax|\btax\b",
            "pension": r"nssf|pension",
            "nhf": r"shif|nhif",
        },
        pension_default_rate=0.06,
    ),
    Country.SOUTH_AFRICA: CountryProfile(
        country=Country.SOUTH_AFRICA,
        currency_code="ZAR",
        currency_symbols=("R", "ZAR", "rand"),
        statutory_deduction_labels={
            "tax": r"paye|income\s*tax|\btax\b",
            "pension": r"pension|provident\s*fund",
            "nhf": r"uif",
        },
        pension_default_rate=0.075,
    ),
}

DEFAULT_COUNTRY = Country.NIGERIA


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------

class ValidationStatus(str, Enum):
    OK = "OK"                       # net salary found directly in the document
    CALCULATED = "CALCULATED"       # net salary derived from gross - deductions
    INCOMPLETE = "INCOMPLETE"       # neither net nor enough components found
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # numbers don't reconcile, flagged for user


@dataclass
class PayslipData:
    employee_name: Optional[str] = None
    employer: Optional[str] = None
    payroll_month: Optional[int] = None
    payroll_year: Optional[int] = None

    basic_salary: float = 0.0
    housing_allowance: float = 0.0
    transport_allowance: float = 0.0
    utility_allowance: float = 0.0
    medical_allowance: float = 0.0
    meal_allowance: float = 0.0
    bonus: float = 0.0
    commission: float = 0.0

    tax: float = 0.0          # PAYE
    pension: float = 0.0
    nhf: float = 0.0          # or SSNIT/NSSF/UIF depending on country profile
    other_deductions: float = 0.0

    gross_salary: float = 0.0
    net_salary: float = 0.0

    currency: str = "NGN"
    country: str = Country.NIGERIA.value

    validation_status: ValidationStatus = ValidationStatus.INCOMPLETE
    extraction_notes: list = field(default_factory=list)

    # Verbatim "Label: Amount" lines lifted straight off the payslip that
    # aren't one of the known structured fields above — e.g. "Rent", "Clothing",
    # "Health". Each item is {"name": str, "amount": float}, in document order.
    # This is what lets envelope names mirror the payslip's own category names.
    line_items: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["validation_status"] = self.validation_status.value
        return d


# --------------------------------------------------------------------------
# Regex library
# --------------------------------------------------------------------------
# Every pattern: optional label variants, optional colon/dash/equals separator,
# then a captured numeric amount. Amounts may contain currency symbols, commas,
# and decimals, which are normalized by `_to_number`.

_NUM = r"((?:₦|GH₵|KSh|NGN|GHS|KES|ZAR|R)?\s?[\d][\d,]*\.?\d*)"

FIELD_PATTERNS: dict[str, str] = {
    "employee_name": r"(?:employee\s*name|staff\s*name|name\s*of\s*employee|employee)\s*[:\-]\s*([A-Za-z][A-Za-z .'\-]{2,60})",
    "employer": r"^(.{3,80}?)(?:\n|LTD|LIMITED|PLC|NIGERIA|GHANA|KENYA)",
    "basic_salary": rf"(?:basic|base)\s*(?:salary|pay)?\s*[:\-=]?\s*{_NUM}",
    "housing_allowance": rf"(?:housing|rent)\s*(?:allowance|pay)?\s*[:\-=]?\s*{_NUM}",
    "transport_allowance": rf"(?:transport|transportation|car)\s*(?:allowance|pay)?\s*[:\-=]?\s*{_NUM}",
    "utility_allowance": rf"(?:utility|utilities)\s*(?:allowance)?\s*[:\-=]?\s*{_NUM}",
    "medical_allowance": rf"(?:medical|health)\s*(?:allowance)?\s*[:\-=]?\s*{_NUM}",
    "meal_allowance": rf"(?:meal|feeding|lunch)\s*(?:allowance)?\s*[:\-=]?\s*{_NUM}",
    "bonus": rf"bonus\s*[:\-=]?\s*{_NUM}",
    "commission": rf"commission\s*[:\-=]?\s*{_NUM}",
    "other_deductions": rf"(?:other\s*deductions?|misc\.?\s*deductions?)\s*[:\-=]?\s*{_NUM}",
    "gross_salary": rf"(?:gross\s*(?:salary|pay|income)?)\s*[:\-=]?\s*{_NUM}",
    "net_salary": rf"(?:net\s*(?:pay|salary|amount|take\s*home)|take\s*home\s*(?:pay)?)\s*[:\-=]?\s*{_NUM}",
}

MONTH_YEAR_PATTERN = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"[,\s]+(\d{4})"
)
MONTH_LOOKUP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _to_number(raw: Optional[str]) -> float:
    """Strip currency symbols / thousands separators, parse to float."""
    if not raw:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", raw)
    if not cleaned:
        return 0.0
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return 0.0


def _search_field(text: str, pattern: str) -> Optional[str]:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


# Distinct, unambiguous detection patterns per country. Deliberately more
# conservative than `currency_symbols` (used for display/stripping) — short
# ASCII letters like South Africa's "R" require a word boundary plus an
# adjacent digit so they don't fire on ordinary English words such as "here".
_DETECTION_PATTERNS: dict[Country, str] = {
    Country.NIGERIA: r"₦|\bNGN\b|\bnaira\b",
    Country.GHANA: r"GH₵|\bGHS\b|\bcedis\b|\bssnit\b",
    Country.KENYA: r"\bKSh\b|\bKES\b|\bshillings\b|\bnssf\b",
    Country.SOUTH_AFRICA: r"\bZAR\b|\brand\b|\buif\b|\bR\s?\d",
}


def detect_country(text: str, default: Country = DEFAULT_COUNTRY) -> Country:
    """Cheap deterministic country detection from currency symbols / codes.
    Falls back to `default` (Nigeria) when nothing matches — this is a
    lookup against known symbol/keyword patterns, not a guess."""
    for country, pattern in _DETECTION_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return country
    return default


def _extract_statutory_deductions(text: str, profile: CountryProfile) -> dict:
    """PAYE / Pension / NHF-equivalent, using the active country's label set."""
    results = {"tax": 0.0, "pension": 0.0, "nhf": 0.0}
    for canonical, label_alt in profile.statutory_deduction_labels.items():
        pattern = rf"(?:{label_alt})\s*(?:\(\d+%?\))?\s*(?:deduction|contribution)?\s*[:\-=]?\s*{_NUM}"
        val = _search_field(text, pattern)
        if val:
            results[canonical] = _to_number(val)
    return results


# --------------------------------------------------------------------------
# Generic line-item extraction (for envelope auto-naming)
# --------------------------------------------------------------------------
# Any "Label: Amount" line that isn't clearly one of the identity/statutory
# fields above is treated as a user-facing budget category — e.g. "Rent",
# "Clothing", "Health" — and its label is kept verbatim (title-cased) so the
# envelope created from it carries exactly the same name the payslip used.

_LINE_ITEM_PATTERN = re.compile(
    rf"^\s*([A-Za-z][A-Za-z0-9 /&'.,()-]{{1,60}}?)\s*[:\-=]\s*{_NUM}"
    r"\s*(?:NGN|GHS|KES|ZAR|naira|cedis|shillings|rand)?\s*$",
    re.IGNORECASE,
)

# Keywords that mark a line as identity info or a statutory figure we already
# capture in a dedicated field — these are excluded so they aren't duplicated
# as a generic line item (case-insensitive substring match on the label).
_LINE_ITEM_EXCLUDE_KEYWORDS = (
    "tax", "paye", "pension", "ssnit", "nssf", "uif", "nhf",
    "gross", "net pay", "net salary", "net take", "take home",
    "employee", "staff name", "employer", "date", "period",
    "payslip", "summary", "reference", "id no", "account no", "bank",
    # Earnings components already captured as structured fields (basic salary,
    # housing/transport/utility/medical/meal allowance, bonus, commission) —
    # excluded here so they aren't *also* turned into envelopes. Envelopes
    # should mirror discretionary budget/spend categories (Rent, Clothing,
    # Health, ...), not restate income components that fund them.
    "basic", "base salary", "salary", "allowance", "bonus", "commission",
)

# Section-header words that sometimes appear before a stray colon but aren't
# themselves budget categories (defensive; the amount-required regex already
# filters most of these out).
_LINE_ITEM_EXCLUDE_EXACT = {"earnings", "deductions", "summary", "details"}


def _extract_line_items(text: str) -> list[dict]:
    items: list[dict] = []
    seen_names: set[str] = set()

    for line in text.splitlines():
        match = _LINE_ITEM_PATTERN.match(line.strip())
        if not match:
            continue

        raw_label, raw_amount = match.group(1), match.group(2)
        normalized = raw_label.strip().lower()

        if normalized in _LINE_ITEM_EXCLUDE_EXACT:
            continue
        if any(keyword in normalized for keyword in _LINE_ITEM_EXCLUDE_KEYWORDS):
            continue

        amount = _to_number(raw_amount)
        if amount <= 0:
            continue

        # Title-case for consistent envelope naming, but preserve short
        # all-caps acronyms (e.g. "DSTV") rather than mangling them.
        display_name = raw_label.strip() if raw_label.isupper() and len(raw_label.strip()) <= 6 else raw_label.strip().title()

        dedupe_key = display_name.lower()
        if dedupe_key in seen_names:
            continue
        seen_names.add(dedupe_key)

        items.append({"name": display_name, "amount": amount})

    return items


def _extract_month_year(text: str) -> tuple[Optional[int], Optional[int]]:
    match = re.search(MONTH_YEAR_PATTERN, text, re.IGNORECASE)
    if not match:
        return None, None
    month_key = match.group(1)[:3].lower()
    month = MONTH_LOOKUP.get(month_key)
    year = int(match.group(2))
    return month, year


def parse_payslip(raw_text: str, country: Optional[Country] = None) -> PayslipData:
    """
    Parse free-form payslip text into a structured PayslipData record.

    Pipeline (entirely deterministic):
      1. Normalize whitespace.
      2. Detect (or accept an explicit) country profile -> currency + labels.
      3. Regex-extract each earnings/deduction field independently.
      4. Regex-extract statutory deductions using the country's label set.
      5. Compute gross salary if not explicitly found.
      6. Compute net salary if not explicitly found (gross - deductions).
      7. Cross-check: if a net salary WAS found in the text, compare it
         against the computed value; flag REVIEW_REQUIRED on mismatch
         beyond a small rounding tolerance, rather than silently trusting
         either number.
    """
    text = re.sub(r"[ \t]+", " ", raw_text).strip()
    notes: list[str] = []

    resolved_country = country or detect_country(text)
    profile = COUNTRY_PROFILES.get(resolved_country, COUNTRY_PROFILES[DEFAULT_COUNTRY])

    data = PayslipData(currency=profile.currency_code, country=profile.country.value)

    # Identity fields
    data.employee_name = _search_field(text, FIELD_PATTERNS["employee_name"])
    employer_match = _search_field(text, FIELD_PATTERNS["employer"])
    data.employer = employer_match.strip() if employer_match else None

    data.payroll_month, data.payroll_year = _extract_month_year(text)

    # Earnings
    data.basic_salary = _to_number(_search_field(text, FIELD_PATTERNS["basic_salary"]))
    data.housing_allowance = _to_number(_search_field(text, FIELD_PATTERNS["housing_allowance"]))
    data.transport_allowance = _to_number(_search_field(text, FIELD_PATTERNS["transport_allowance"]))
    data.utility_allowance = _to_number(_search_field(text, FIELD_PATTERNS["utility_allowance"]))
    data.medical_allowance = _to_number(_search_field(text, FIELD_PATTERNS["medical_allowance"]))
    data.meal_allowance = _to_number(_search_field(text, FIELD_PATTERNS["meal_allowance"]))
    data.bonus = _to_number(_search_field(text, FIELD_PATTERNS["bonus"]))
    data.commission = _to_number(_search_field(text, FIELD_PATTERNS["commission"]))

    # Statutory deductions (country-aware)
    statutory = _extract_statutory_deductions(text, profile)
    data.tax = statutory["tax"]
    data.pension = statutory["pension"]
    data.nhf = statutory["nhf"]
    data.other_deductions = _to_number(_search_field(text, FIELD_PATTERNS["other_deductions"]))

    # Gross salary
    explicit_gross = _to_number(_search_field(text, FIELD_PATTERNS["gross_salary"]))
    computed_gross = (
        data.basic_salary + data.housing_allowance + data.transport_allowance +
        data.utility_allowance + data.medical_allowance + data.meal_allowance +
        data.bonus + data.commission
    )
    if explicit_gross > 0:
        data.gross_salary = explicit_gross
        if computed_gross > 0 and abs(explicit_gross - computed_gross) > max(1.0, explicit_gross * 0.01):
            notes.append(
                f"Stated gross ({explicit_gross:,.2f}) differs from the sum of "
                f"extracted earnings ({computed_gross:,.2f}); using the stated figure."
            )
    else:
        data.gross_salary = computed_gross
        if computed_gross > 0:
            notes.append("Gross salary was not stated explicitly; computed as the sum of extracted earnings.")

    total_deductions = data.tax + data.pension + data.nhf + data.other_deductions

    # Net salary
    explicit_net = _to_number(_search_field(text, FIELD_PATTERNS["net_salary"]))
    computed_net = max(0.0, data.gross_salary - total_deductions)

    if explicit_net > 0:
        data.net_salary = explicit_net
        if data.gross_salary > 0:
            if abs(explicit_net - computed_net) <= max(1.0, explicit_net * 0.01):
                data.validation_status = ValidationStatus.OK
            else:
                data.validation_status = ValidationStatus.REVIEW_REQUIRED
                notes.append(
                    f"Stated net ({explicit_net:,.2f}) does not reconcile with "
                    f"gross minus deductions ({computed_net:,.2f}). Please review."
                )
        else:
            data.validation_status = ValidationStatus.OK
    elif computed_net > 0:
        data.net_salary = computed_net
        data.validation_status = ValidationStatus.CALCULATED
        notes.append("Net salary was not stated explicitly; calculated as gross minus deductions.")
    else:
        data.validation_status = ValidationStatus.INCOMPLETE
        notes.append("Could not determine net salary: no explicit figure and insufficient earnings data.")

    # Generic line items, kept with the same names used on the payslip itself
    # (e.g. Rent, Clothing, Health) — see `_extract_line_items` above.
    data.line_items = _extract_line_items(text)
    if data.line_items:
        names = ", ".join(li["name"] for li in data.line_items)
        notes.append(f"Detected {len(data.line_items)} envelope-style line item(s) on the payslip: {names}.")

    data.extraction_notes = notes
    return data
