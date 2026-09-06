"""Billing: advertiser balance top-ups, adjustments, and the transaction ledger.

Money model (audit M-01/M-02):

* The advertiser balance can only change here (finance roles) or in
  ``billing_core.bill_win`` (RTB spend). Advertiser CRUD never touches it.
* Every operation is one DB transaction: ``SELECT ... FOR UPDATE`` on the
  advertiser row, ``UPDATE`` of the balance, ``INSERT`` into the append-only
  ledger. A failure anywhere rolls the whole thing back.
* Amounts are ``Decimal`` quantized to kopecks at the boundary.
* An ``Idempotency-Key`` header (or ``idempotency_key`` body field) makes a
  retried request return the original ledger row instead of crediting twice.
  The uniqueness is enforced by ``uq_transactions_idempotency_key``.
"""
from decimal import Decimal, ROUND_HALF_UP

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from db import get_pool
from rbac import require_billing_viewer, require_finance_write

router = APIRouter(prefix="/billing", tags=["billing"])

_KOPECK = Decimal("0.01")
_MAX_AMOUNT = Decimal("1000000")
_IDEMPOTENCY_KEY_MAX = 128


def _to_money(value: Decimal) -> Decimal:
    return value.quantize(_KOPECK, rounding=ROUND_HALF_UP)


class DepositIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advertiser_id: str
    amount: Decimal = Field(gt=0, le=_MAX_AMOUNT, max_digits=14, decimal_places=2)
    description: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=_IDEMPOTENCY_KEY_MAX)


class AdjustmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advertiser_id: str
    amount: Decimal = Field(ge=-_MAX_AMOUNT, le=_MAX_AMOUNT, max_digits=14, decimal_places=2)
    description: str = Field(min_length=1, max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=_IDEMPOTENCY_KEY_MAX)


def _ledger_response(row, *, replayed: bool) -> dict:
    return {
        "transaction_id": row["id"],
        "advertiser_id": str(row["advertiser_id"]),
        "type": row["type"],
        "amount": float(row["amount"]),
        "balance": float(row["balance_after"]),
        "replayed": replayed,
    }


async def _find_by_key(conn, key: str):
    return await conn.fetchrow(
        "SELECT id, advertiser_id, type, amount, balance_after FROM transactions WHERE idempotency_key = $1",
        key,
    )


async def _apply_money_operation(
    *,
    user: dict,
    advertiser_id: str,
    amount: Decimal,
    op_type: str,
    description: str,
    idempotency_key: str | None,
    allow_negative_result: bool,
) -> dict:
    if amount == 0:
        raise HTTPException(status_code=422, detail="Сумма операции не может быть нулевой")
    amount = _to_money(amount)
    pool = await get_pool()

    async with pool.acquire() as conn:
        if idempotency_key:
            existing = await _find_by_key(conn, idempotency_key)
            if existing is not None:
                return _replay(existing, advertiser_id, op_type, amount)

        try:
            async with conn.transaction():
                adv = await conn.fetchrow(
                    "SELECT id, balance FROM advertisers WHERE id = $1 AND user_id = $2 FOR UPDATE",
                    advertiser_id, user["id"],
                )
                if adv is None:
                    raise HTTPException(status_code=404, detail="Рекламодатель не найден")

                new_balance = Decimal(adv["balance"]) + amount
                if new_balance < 0 and not allow_negative_result:
                    raise HTTPException(
                        status_code=409,
                        detail="Недостаточно средств: корректировка уводит баланс в минус",
                    )

                await conn.execute(
                    "UPDATE advertisers SET balance = $2, updated_at = now() WHERE id = $1",
                    adv["id"], new_balance,
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO transactions
                        (user_id, advertiser_id, type, amount, balance_after, description, idempotency_key)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id, advertiser_id, type, amount, balance_after
                    """,
                    user["id"], adv["id"], op_type, amount, new_balance, description, idempotency_key,
                )
        except asyncpg.UniqueViolationError:
            # Lost the race against a concurrent request with the same key:
            # the transaction above rolled back, return the winner's row.
            existing = await _find_by_key(conn, idempotency_key) if idempotency_key else None
            if existing is None:
                raise
            return _replay(existing, advertiser_id, op_type, amount)

    return _ledger_response(row, replayed=False)


def _replay(existing, advertiser_id: str, op_type: str, amount: Decimal) -> dict:
    same = (
        str(existing["advertiser_id"]) == advertiser_id
        and existing["type"] == op_type
        and Decimal(existing["amount"]) == amount
    )
    if not same:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key уже использован для другой операции",
        )
    return _ledger_response(existing, replayed=True)


def _pick_key(header_key: str | None, body_key: str | None) -> str | None:
    key = body_key or header_key
    if key is None:
        return None
    key = key.strip()
    if not key:
        return None
    if len(key) > _IDEMPOTENCY_KEY_MAX:
        raise HTTPException(status_code=422, detail="Idempotency-Key слишком длинный")
    return key


@router.post("/deposit", status_code=201)
async def deposit(
    body: DepositIn,
    user: dict = Depends(require_finance_write),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Top up an advertiser balance (manual / offline payment flow)."""
    return await _apply_money_operation(
        user=user,
        advertiser_id=body.advertiser_id,
        amount=body.amount,
        op_type="deposit",
        description=body.description or "Пополнение баланса",
        idempotency_key=_pick_key(idempotency_key, body.idempotency_key),
        allow_negative_result=False,
    )


@router.post("/adjust", status_code=201)
async def adjust(
    body: AdjustmentIn,
    user: dict = Depends(require_finance_write),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Manual balance adjustment (credit or debit) with a mandatory reason."""
    return await _apply_money_operation(
        user=user,
        advertiser_id=body.advertiser_id,
        amount=body.amount,
        op_type="adjustment",
        description=body.description,
        idempotency_key=_pick_key(idempotency_key, body.idempotency_key),
        allow_negative_result=False,
    )


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
