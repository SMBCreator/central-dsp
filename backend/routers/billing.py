"""Billing: advertiser balance top-ups, adjustments, and the transaction ledger."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import current_user  # noqa: F401 — оставлен для совместимости импортов
from rbac import require_billing_viewer, require_finance_write
from db import get_pool

router = APIRouter(prefix="/billing", tags=["billing"])


class DepositIn(BaseModel):
    advertiser_id: str
    amount: float = Field(gt=0, le=1_000_000)
    description: str | None = None


class AdjustmentIn(BaseModel):
    advertiser_id: str
    amount: float = Field(ge=-1_000_000, le=1_000_000)
    description: str


@router.post("/deposit", status_code=201)
async def deposit(body: DepositIn, user: dict = Depends(require_finance_write)):
    """Top up an advertiser balance (manual / offline payment flow)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE advertisers SET balance = balance + $3, updated_at = now()
        WHERE id = $1 AND user_id = $2
        RETURNING id, balance
        """,
        body.advertiser_id, user["id"], body.amount,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    await pool.execute(
        """
        INSERT INTO transactions (user_id, advertiser_id, type, amount, balance_after, description)
        VALUES ($1, $2, 'deposit', $3, $4, $5)
        """,
        user["id"], body.advertiser_id, body.amount, float(row["balance"]),
        body.description or "Пополнение баланса",
    )
    return {"advertiser_id": str(row["id"]), "balance": float(row["balance"])}


@router.post("/adjust", status_code=201)
async def adjust(body: AdjustmentIn, user: dict = Depends(require_finance_write)):
    """Manual balance adjustment (credit or debit) with a mandatory reason."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE advertisers SET balance = balance + $3, updated_at = now()
        WHERE id = $1 AND user_id = $2
        RETURNING id, balance
        """,
        body.advertiser_id, user["id"], body.amount,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    await pool.execute(
        """
        INSERT INTO transactions (user_id, advertiser_id, type, amount, balance_after, description)
        VALUES ($1, $2, 'adjustment', $3, $4, $5)
        """,
        user["id"], body.advertiser_id, body.amount, float(row["balance"]), body.description,
    )
    return {"advertiser_id": str(row["id"]), "balance": float(row["balance"])}


@router.get("/transactions")
async def transactions(advertiser_id: str | None = None, limit: int = 100, user: dict = Depends(require_billing_viewer)):
    pool = await get_pool()
    limit = min(max(limit, 1), 500)
    if advertiser_id:
        rows = await pool.fetch(
            """
            SELECT t.*, a.name AS advertiser_name FROM transactions t
            LEFT JOIN advertisers a ON a.id = t.advertiser_id
            WHERE t.user_id = $1 AND t.advertiser_id = $2
            ORDER BY t.created_at DESC LIMIT $3
            """,
            user["id"], advertiser_id, limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT t.*, a.name AS advertiser_name FROM transactions t
            LEFT JOIN advertisers a ON a.id = t.advertiser_id
            WHERE t.user_id = $1
            ORDER BY t.created_at DESC LIMIT $2
            """,
            user["id"], limit,
        )
    return [
        {
            "id": r["id"],
            "advertiser_id": str(r["advertiser_id"]) if r["advertiser_id"] else None,
            "advertiser_name": r["advertiser_name"],
            "type": r["type"],
            "amount": float(r["amount"]),
            "balance_after": float(r["balance_after"]) if r["balance_after"] is not None else None,
            "description": r["description"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/summary")
async def billing_summary(user: dict = Depends(require_billing_viewer)):
    """Balances of all advertisers plus total spend from the ledger."""
    pool = await get_pool()
    advertisers = await pool.fetch(
        "SELECT id, name, balance, currency, status FROM advertisers WHERE user_id = $1 ORDER BY name",
        user["id"],
    )
    totals = await pool.fetchrow(
        """
        SELECT
          COALESCE(SUM(CASE WHEN type = 'deposit' THEN amount END), 0)    AS deposited,
          COALESCE(SUM(CASE WHEN type = 'spend' THEN -amount END), 0)     AS spent,
          COALESCE(SUM(CASE WHEN type = 'adjustment' THEN amount END), 0) AS adjusted
        FROM transactions WHERE user_id = $1
        """,
        user["id"],
    )
    return {
        "advertisers": [
            {
                "id": str(a["id"]), "name": a["name"], "status": a["status"],
                "balance": float(a["balance"]), "currency": a["currency"],
            }
            for a in advertisers
        ],
        "total_deposited": float(totals["deposited"]),
        "total_spent": float(totals["spent"]),
        "total_adjusted": float(totals["adjusted"]),
    }
