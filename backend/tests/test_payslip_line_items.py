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


def test_line_items_capture_verbatim_payslip_names():
    result = parse_payslip(PAYSLIP_WITH_BUDGET_SPLIT)
    names = [item["name"] for item in result.line_items]
    assert names == ["Rent", "Clothing", "Health", "DSTV"]


def test_line_items_exclude_structured_earnings_fields():
    """Basic Salary / Housing Allowance are already captured as dedicated
    fields; they should not also show up as generic line items (that would
    double-count them as budget-destination envelopes)."""
    result = parse_payslip(PAYSLIP_WITH_BUDGET_SPLIT)
    names_lower = {item["name"].lower() for item in result.line_items}
    assert "basic salary" not in names_lower
    assert "housing allowance" not in names_lower


def test_line_items_exclude_statutory_and_identity_fields():
    result = parse_payslip(PAYSLIP_WITH_BUDGET_SPLIT)
    names_lower = {item["name"].lower() for item in result.line_items}
    assert not any("tax" in n or "pension" in n for n in names_lower)


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
