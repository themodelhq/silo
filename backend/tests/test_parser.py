import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parser import parse_payslip, Country, ValidationStatus, detect_country

NIGERIAN_PAYSLIP_1 = """
COMPASS GLOBAL TECHNOLOGIES NIGERIA LTD
Employee Name: Adebayo Benson
Date: June 26, 2026

EARNINGS:
Basic Salary: 450,000.00 NGN
Housing Allowance: 150,000.00
Transport Allowance: 80,000.00
Utility Allowance: 30,000.00

DEDUCTIONS:
PAYE Tax: 55,000.00
Pension Deduction: 36,000.00

NET TAKE HOME: 619,000.00
"""

NIGERIAN_PAYSLIP_2_NO_NET = """
SEAMLESS HR CLIENT CORP (LAGOS OFFICE)
PAYSLIP SUMMARY FOR: CHIOMA OKOYE

BASE SALARY: ₦800,000
CAR ALLOWANCE: ₦120,000
RENT ALLOWANCE: ₦250,000

TAX: ₦110,000
PENSION (8%): ₦64,000
"""

GHANA_PAYSLIP = """
ACCRA MERIDIAN LTD
Employee Name: Kwame Mensah
Basic Salary: GHS 8,000.00
SSNIT Deduction: 440.00
NET PAY: GHS 7,200.00
"""


def test_parses_explicit_net_salary_ok_status():
    result = parse_payslip(NIGERIAN_PAYSLIP_1)
    assert result.net_salary == 619000.00
    assert result.validation_status == ValidationStatus.OK
    assert result.employee_name == "Adebayo Benson"
    assert result.tax == 55000.00
    assert result.pension == 36000.00


def test_calculates_net_when_missing():
    result = parse_payslip(NIGERIAN_PAYSLIP_2_NO_NET)
    expected_gross = 800000 + 120000 + 250000
    expected_deductions = 110000 + 64000
    assert result.gross_salary == expected_gross
    assert result.net_salary == expected_gross - expected_deductions
    assert result.validation_status == ValidationStatus.CALCULATED


def test_country_detection_defaults_to_nigeria():
    assert detect_country("no currency symbols here") == Country.NIGERIA


def test_country_detection_ghana():
    result = parse_payslip(GHANA_PAYSLIP, country=Country.GHANA)
    assert result.currency == "GHS"
    assert result.pension == 440.00
    assert result.net_salary == 7200.00


def test_flags_mismatched_net_for_review():
    text = """
    Basic Salary: 100,000
    Tax: 10,000
    Pension: 5,000
    Net Pay: 999,999
    """
    result = parse_payslip(text)
    assert result.validation_status == ValidationStatus.REVIEW_REQUIRED


def test_incomplete_when_no_usable_data():
    result = parse_payslip("This document contains no payroll figures at all.")
    assert result.validation_status == ValidationStatus.INCOMPLETE
    assert result.net_salary == 0.0
