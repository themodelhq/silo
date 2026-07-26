import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parser import parse_payslip


PAYSLIP_WITH_BUDGET_SPLIT = """
COMPASS GLOBAL TECHNOLOGIES NIGERIA LTD
Employee Name: Jane Doe

EARNINGS:
Basic Salary: 300,000.00 NGN
Housing Allowance: 50,000.00

DEDUCTIONS:
PAYE Tax: 20,000.00
Pension Deduction: 10,000.00

BUDGET SPLIT:
Rent: 100,000.00
Clothing: 20,000.00
Health: 15,000.00
DSTV: 12,000.00

NET TAKE HOME: 320,000.00
"""

# A real-world sample (tabular, whitespace-separated, no colons) where every
# earnings line is named "X Allowance" — including one that also happens to
# contain the word "pension" despite not being the statutory deduction.
PAYSLIP_TABULAR_ALL_ALLOWANCES = """
** EARNINGS *** AMOUNT(₦)
------------------------------------------------------------
Basic Allowance 144,375.00
Housing Allowance 86,625.00
Transport Allowance 28,875.00
Other Income Pension Allowance 17,325.00
------------------------------------------------------------
GROSS SALARY
277,200.00
============================================================
*** DEDUCTIONS *** AMOUNT(₦)
---------------------------------------------------
PAYE Tax 20,000.00
Employee Pension 10,000.00
NHF Deduction 2,000.00
--------------------------------------------------
NET SALARY FOR APRIL 2020 245,200.00
"""


def test_line_items_capture_verbatim_payslip_names():
    result = parse_payslip(PAYSLIP_WITH_BUDGET_SPLIT)
    names = [item["name"] for item in result.line_items]
    # Every earnings/budget line becomes its own envelope, in document order —
    # including Basic Salary and Housing Allowance, which are also captured
    # in the structured fields for the gross/net summary.
    assert names == ["Basic Salary", "Housing Allowance", "Rent", "Clothing", "Health", "DSTV"]


def test_line_items_include_all_named_earnings_even_when_also_structured_fields():
    """Basic Salary / Housing Allowance ARE captured as dedicated structured
    fields (for the gross/net summary) AND as line items (so an envelope is
    created for them too) — every allocation on the payslip becomes an
    envelope, not a filtered subset."""
    result = parse_payslip(PAYSLIP_WITH_BUDGET_SPLIT)
    names_lower = {item["name"].lower() for item in result.line_items}
    assert "basic salary" in names_lower
    assert "housing allowance" in names_lower
    assert result.basic_salary == 300000.0
    assert result.housing_allowance == 50000.0


def test_line_items_exclude_statutory_and_identity_fields():
    result = parse_payslip(PAYSLIP_WITH_BUDGET_SPLIT)
    names_lower = {item["name"].lower() for item in result.line_items}
    assert not any("tax" in n or "pension deduction" in n for n in names_lower)


def test_line_items_amounts_are_correct():
    result = parse_payslip(PAYSLIP_WITH_BUDGET_SPLIT)
    by_name = {item["name"]: item["amount"] for item in result.line_items}
    assert by_name["Rent"] == 100000.0
    assert by_name["Clothing"] == 20000.0
    assert by_name["Health"] == 15000.0
    assert by_name["DSTV"] == 12000.0


def test_no_false_positive_line_items_on_plain_text():
    result = parse_payslip("This document contains no payroll figures at all.")
    assert result.line_items == []


def test_line_items_deduplicated_case_insensitively():
    text = """
    Rent: 50,000
    RENT: 60,000
    """
    result = parse_payslip(text)
    assert len(result.line_items) == 1
    assert result.line_items[0]["amount"] == 50000.0


def test_tabular_whitespace_separated_payslip_captures_every_earnings_line():
    """Regression test for a real-world payslip format: tabular rows with no
    colon separator ("Label   Amount"), where the base pay is called "Basic
    Allowance" rather than "Basic Salary", and one earnings line ("Other
    Income Pension Allowance") contains the word "pension" without being the
    statutory pension deduction."""
    result = parse_payslip(PAYSLIP_TABULAR_ALL_ALLOWANCES)

    assert result.basic_salary == 144375.0, "Basic Allowance should be recognized as basic salary"

    names = [item["name"] for item in result.line_items]
    assert names == ["Basic Allowance", "Housing Allowance", "Transport Allowance", "Other Income Pension Allowance"]

    total = sum(item["amount"] for item in result.line_items)
    assert total == 277200.0, "line items should sum to the stated gross salary"

    # The genuine statutory deductions must still be excluded.
    names_lower = {n.lower() for n in names}
    assert "paye tax" not in names_lower
    assert "employee pension" not in names_lower
    assert "nhf deduction" not in names_lower
