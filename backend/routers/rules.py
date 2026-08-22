from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from rbac import require_platform_write
from db import get_pool
from rules_engine import evaluate_rules_for_user
from schemas import RuleIn, RulePatch

router = APIRouter(prefix="/rules", tags=["rules"])


def _serialize(row) -> dict:
    d = dict(row)
    for k in ("id", "user_id", "campaign_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("threshold", "action_value"):
        if d.get(k) is not None:
            d[k] = float(d[k])
    return d


@router.get("")
async def list_rules(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM rules WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
    )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_rule(body: RuleIn, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    if body.campaign_id:
        camp = await pool.fetchval(
            "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", body.campaign_id, user["id"]
        )
        if not camp:
            raise HTTPException(status_code=404, detail="Кампания не найдена")
    row = await pool.fetchrow(
        """
        INSERT INTO rules (user_id, campaign_id, name, metric, operator, threshold, action, action_value, window_hours)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *
        """,
        user["id"], body.campaign_id, body.name, body.metric, body.operator,
        body.threshold, body.action, body.action_value, body.window_hours,
    )
    return _serialize(row)


@router.patch("/{rule_id}")
async def update_rule(rule_id: str, body: RulePatch, user: dict = Depends(require_platform_write)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    pool = await get_pool()
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        sets.append(f"{k} = ${i}")
        values.append(v)
    row = await pool.fetchrow(
        f"UPDATE rules SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        rule_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    return _serialize(row)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM rules WHERE id = $1 AND user_id = $2", rule_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Правило не найдено")


@router.post("/evaluate")
async def evaluate_now(user: dict = Depends(require_platform_write)):
    """Run all active rules for the current user immediately."""
    triggered = await evaluate_rules_for_user(str(user["id"]))
    return {"triggered": triggered}
