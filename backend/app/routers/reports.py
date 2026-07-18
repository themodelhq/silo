"""Report endpoints. All aggregation is plain SQL/Python arithmetic —
sums, grouping, and date-range filters — never inference."""

from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models, auth
from ..database import get_db

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/cash-flow")
def cash_flow(
    period: str = Query(default="monthly", pattern="^(daily|weekly|monthly|quarterly|annual)$"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    days_lookup = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 90, "annual": 365}
    since = datetime.utcnow() - timedelta(days=days_lookup[period])

    txns = (
        db.query(models.Transaction)
        .filter(models.Transaction.user_id == current_user.id, models.Transaction.occurred_at >= since)
        .all()
    )

    income = sum(t.amount for t in txns if t.type in ("income", "refund"))
    expenses = sum(t.amount for t in txns if t.type == "expense")

    return {
        "period": period,
        "since": since.isoformat(),
        "total_income": round(income, 2),
        "total_expenses": round(expenses, 2),
        "net_cash_flow": round(income - expenses, 2),
        "transaction_count": len(txns),
    }


@router.get("/expense-categories")
def expense_categories(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    txns = (
        db.query(models.Transaction)
        .filter(models.Transaction.user_id == current_user.id, models.Transaction.type == "expense")
        .all()
    )
    totals: dict[str, float] = defaultdict(float)
    for t in txns:
        totals[t.category or "Uncategorized"] += t.amount

    return [{"category": k, "total": round(v, 2)} for k, v in sorted(totals.items(), key=lambda kv: -kv[1])]


@router.get("/envelope-allocation")
def envelope_allocation(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    envelopes = db.query(models.Envelope).filter(
        models.Envelope.user_id == current_user.id, models.Envelope.archived == False  # noqa: E712
    ).all()
    total_allocated = sum(e.allocated for e in envelopes) or 1  # avoid div by zero
    return [
        {
            "name": e.name,
            "allocated": round(e.allocated, 2),
            "balance": round(e.balance, 2),
            "spent": round(e.allocated - e.balance, 2),
            "percent_of_budget": round((e.allocated / total_allocated) * 100, 1),
        }
        for e in envelopes
    ]


@router.get("/budget-performance")
def budget_performance(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    envelopes = db.query(models.Envelope).filter(models.Envelope.user_id == current_user.id).all()
    results = []
    for e in envelopes:
        spent = e.allocated - e.balance
        pct_used = round((spent / e.allocated) * 100, 1) if e.allocated > 0 else 0.0
        status_flag = "over_budget" if e.balance < 0 else ("on_track" if pct_used < 90 else "near_limit")
        results.append({
            "name": e.name, "allocated": round(e.allocated, 2), "spent": round(spent, 2),
            "remaining": round(e.balance, 2), "percent_used": pct_used, "status": status_flag,
        })
    return results
