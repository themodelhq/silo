"""Pydantic schemas for API request/response validation."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator


# ---- Auth ----------------------------------------------------------

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str] = None
    # Optional at signup, but required before a Paystack dedicated virtual
    # account can be created (see routers/payments.py) — can be added later
    # via PATCH /auth/me.
    phone: Optional[str] = None
    country: str = "NG"

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str]
    phone: Optional[str]
    country: str
    currency: str

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    """PATCH /auth/me — currently just full_name/phone, since phone is
    required to create a Paystack dedicated virtual account and a user may
    not have supplied it at signup."""
    full_name: Optional[str] = None
    phone: Optional[str] = None


class AdminUserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str]
    phone: Optional[str]
    country: str
    currency: str
    is_active: bool
    is_admin: bool
    auth_provider: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Payslips --------------------------------------------------------

class PayslipParseRequest(BaseModel):
    raw_text: str = Field(min_length=10, description="Extracted/pasted payslip text")
    country: Optional[str] = None  # ISO code override; auto-detected if omitted


class PayslipLineItemOut(BaseModel):
    name: str
    amount: float


class PayslipOut(BaseModel):
    id: str
    employer_name: Optional[str]
    payroll_month: Optional[int]
    payroll_year: Optional[int]
    basic_salary: float
    housing_allowance: float
    transport_allowance: float
    utility_allowance: float
    medical_allowance: float
    meal_allowance: float
    bonus: float
    commission: float
    tax: float
    pension: float
    nhf: float
    other_deductions: float
    gross_salary: float
    net_salary: float
    currency: str
    country: str
    validation_status: str
    extraction_notes: list[str]
    line_items: list[PayslipLineItemOut]

    class Config:
        from_attributes = True


class GeneratedEnvelopeSplitOut(BaseModel):
    """Returned alongside a parsed payslip: the envelope rules that were just
    (re)generated from its line items, so the client can show "here's your
    new split" immediately after upload."""
    envelope_name: str
    allocation_type: str
    value: float
    color: str
    priority: int


# ---- Envelopes & rules ----------------------------------------------

class PayslipParseResponse(BaseModel):
    """What POST /payslips/parse returns: the saved payslip plus the envelope
    split that was just generated from its line items (if any)."""
    payslip: PayslipOut
    generated_envelope_split: list[GeneratedEnvelopeSplitOut]


class EnvelopeRuleIn(BaseModel):
    envelope_name: str
    allocation_type: str  # PERCENTAGE | FIXED | REMAINDER
    value: float = 0.0
    color: str = "#6366F1"
    priority: int = 99


class EnvelopeRuleOut(EnvelopeRuleIn):
    id: str
    active: bool
    source_payslip_id: Optional[str] = None

    class Config:
        from_attributes = True


class EnvelopeOut(BaseModel):
    id: str
    name: str
    balance: float
    allocated: float
    color: str
    priority: int
    locked: bool
    archived: bool
    recurring: bool

    class Config:
        from_attributes = True


class EnvelopeCreate(BaseModel):
    name: str
    color: str = "#6366F1"
    priority: int = 99


class EnvelopeRename(BaseModel):
    new_name: str


class EnvelopeTransfer(BaseModel):
    from_envelope_id: str
    to_envelope_id: str
    amount: float = Field(gt=0)


class EnvelopeMerge(BaseModel):
    source_envelope_id: str
    target_envelope_id: str


class EnvelopeSplit(BaseModel):
    source_envelope_id: str
    new_name: str
    fraction: float = Field(gt=0, lt=1)


# ---- Transactions ------------------------------------------------------

class TransactionCreate(BaseModel):
    envelope_id: Optional[str] = None
    type: str  # expense | income | transfer | refund
    amount: float = Field(gt=0)
    category: Optional[str] = None
    merchant: Optional[str] = None
    note: Optional[str] = None
    occurred_at: Optional[datetime] = None


class TransactionOut(BaseModel):
    id: str
    envelope_id: Optional[str]
    type: str
    amount: float
    category: Optional[str]
    merchant: Optional[str]
    note: Optional[str]
    occurred_at: datetime

    class Config:
        from_attributes = True


# ---- Payments (virtual account / dedicated account numbers) -----------

class PaymentAccountCreateRequest(BaseModel):
    """Body for POST /payments/accounts. All fields optional:
    - `phone` only needs to be sent if the user's profile doesn't have one yet
      (it will also be saved to their profile).
    - `bvn` / `bank_code` / `account_number` are the identity-validation
      fields Paystack requires for businesses in the Financial Services,
      Betting, or General Services categories. Omit them entirely if your
      Paystack business isn't in one of those categories — Paystack will
      simply create the account without the validation step ("optional
      compliance" path). None of these are ever persisted to the database;
      they're forwarded to Paystack and then discarded.
    """
    phone: Optional[str] = None
    bvn: Optional[str] = None
    bank_code: Optional[str] = None
    account_number: Optional[str] = None
    preferred_bank: Optional[str] = None


class PaymentAccountOut(BaseModel):
    id: str
    provider: str
    account_number: Optional[str]
    account_name: Optional[str]
    bank_name: Optional[str]
    currency: str
    status: str
    failure_reason: Optional[str]
    active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PaymentEventOut(BaseModel):
    id: str
    provider: str
    provider_reference: str
    amount: float
    account_number: Optional[str]
    status: str
    split_breakdown: dict
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Admin -------------------------------------------------------------

class AdminStatsOut(BaseModel):
    total_users: int
    active_users: int
    admin_users: int
    total_payslips: int
    total_envelopes: int
    total_transactions: int
    total_payment_accounts: int
    total_payment_events: int
