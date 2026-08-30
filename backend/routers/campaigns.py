from datetime import date

import json
from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from rbac import can_see_all, require_campaign_editor, require_campaign_write
from db import get_pool
from schemas import CampaignIn, CampaignPatch

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

_NUMERIC = {"daily_budget", "total_budget", "spent_today", "spent_total"}
_DATES = {"start_date", "end_date"}


def _serialize(row) -> dict:
    d = dict(row)
    if isinstance(d.get("frequency_caps"), str):
        try:
            d["frequency_caps"] = json.loads(d["frequency_caps"])
        except Exception:
            d["frequency_caps"] = None
    for k in ("id", "advertiser_id", "user_id"):
        d[k] = str(d[k])
    for k in ("group_id", "pixel_id", "goal_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in _NUMERIC:
        if k in d and d[k] is not None:
            d[k] = float(d[k])
    for k in _DATES:
        if d.get(k):
            d[k] = d[k].isoformat()
    return d


def _parse_date(val: str | None) -> date | None:
    return date.fromisoformat(val) if val else None


@router.get("")
async def list_campaigns(user: dict = Depends(current_user)):
    pool = await get_pool()
    if can_see_all(user):
        rows = await pool.fetch(
            """
            SELECT c.*, a.name AS advertiser_name
            FROM campaigns c JOIN advertisers a ON a.id = c.advertiser_id
            ORDER BY c.created_at DESC
            """
        )
    else:
        rows = await pool.fetch(
            """
            SELECT c.*, a.name AS advertiser_name
            FROM campaigns c JOIN advertisers a ON a.id = c.advertiser_id
            WHERE c.user_id = $1 ORDER BY c.created_at DESC
            """,
            user["id"],
        )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_campaign(body: CampaignIn, user: dict = Depends(require_campaign_editor)):
    pool = await get_pool()
    adv = await pool.fetchval(
        "SELECT 1 FROM advertisers WHERE id = $1 AND user_id = $2", body.advertiser_id, user["id"]
    )
    if not adv:
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    row = await pool.fetchrow(
        """
        INSERT INTO campaigns (advertiser_id, user_id, name, objective, daily_budget, total_budget,
                               start_date, end_date, pacing, frequency_cap_impressions, frequency_cap_period_hours,
                               group_id, kktu_code, kktu_label, bid_shading, frequency_caps)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) RETURNING *
        """,
        body.advertiser_id, user["id"], body.name, body.objective, body.daily_budget,
        body.total_budget, _parse_date(body.start_date), _parse_date(body.end_date),
        body.pacing, body.frequency_cap_impressions, body.frequency_cap_period_hours, body.group_id,
        body.kktu_code, body.kktu_label, body.bid_shading,
        json.dumps([w.model_dump() for w in body.frequency_caps]) if body.frequency_caps else None,
    )
    return _serialize(row)


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return _serialize(row)


@router.patch("/{campaign_id}")
async def update_campaign(campaign_id: str, body: CampaignPatch, user: dict = Depends(require_campaign_editor)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    for k in _DATES:
        if k in updates:
            updates[k] = _parse_date(updates[k])
    if "frequency_caps" in updates:
        updates["frequency_caps"] = json.dumps(updates["frequency_caps"])
    pool = await get_pool()
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        sets.append(f"{k} = ${i}")
        values.append(v)
    # Launch guard (ФЗ-347): кампания не активируется, пока у всех её
    # креативов нет erid (маркировка через ОРД). ORD_LAUNCH_GUARD=off — только dev.
    import os as _os
    if updates.get("status") == "active" and _os.getenv("ORD_LAUNCH_GUARD", "on") != "off":
        # Гуард действует только для РФ-трафика: Россия явно выбрана в гео
        # хотя бы одного line item, или гео не ограничено (весь мир включает РФ).
        targets_ru = await pool.fetchval(
            """SELECT bool_or(
                     COALESCE(jsonb_array_length(targeting->'geo_countries'), 0) = 0
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(targeting->'geo_countries') g
                                WHERE upper(g) IN ('RU','RUS'))
                   )
               FROM line_items WHERE campaign_id = $1""",
            campaign_id,
        )
        unmarked = 0
        if targets_ru is None or targets_ru:  # нет line items = считаем, что РФ возможна
            unmarked = await pool.fetchval(
                """SELECT count(*) FROM creatives
                   WHERE campaign_id = $1 AND (erid IS NULL OR erid = '')""",
                campaign_id,
            )
        if unmarked:
            raise HTTPException(
                status_code=409,
                detail=f"Нельзя активировать: {unmarked} креатив(ов) без erid. "
                       "Зарегистрируйте креативы в ОРД (раздел Маркировка).",
            )
    row = await pool.fetchrow(
        f"UPDATE campaigns SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        campaign_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return _serialize(row)


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: str, user: dict = Depends(require_campaign_editor)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Кампания не найдена")


@router.get("/{campaign_id}/diagnostics")
async def campaign_diagnostics(campaign_id: str, user: dict = Depends(current_user)):
    """Почему кампания не откручивается: пошаговая проверка условий участия в торгах.

    Зеркалит JOIN-условия фида для биддера (routers/bidder_feed.campaigns_feed),
    поэтому диагностика совпадает с реальной логикой отбора кандидатов. Каждый
    пункт — {code, ok, severity, title, detail}. severity: 'blocker' полностью
    останавливает показы, 'warning' — сужает охват.
    """
    pool = await get_pool()
    camp = await pool.fetchrow(
        "SELECT * FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if not camp:
        raise HTTPException(status_code=404, detail="Кампания не найдена")

    checks: list[dict] = []

    def add(code, ok, severity, title, detail):
        checks.append(
            {"code": code, "ok": bool(ok), "severity": severity, "title": title, "detail": detail}
        )

    # 1. Статус кампании
    add(
        "campaign_status",
        camp["status"] == "active",
        "blocker",
        "Статус кампании",
        "Кампания активна" if camp["status"] == "active"
        else f"Кампания в статусе «{camp['status']}». Запустите её, чтобы участвовать в торгах.",
    )

    # 2. Даты открутки
    today = date.today()
    start_ok = camp["start_date"] is None or camp["start_date"] <= today
    end_ok = camp["end_date"] is None or camp["end_date"] >= today
    add(
        "schedule",
        start_ok and end_ok,
        "blocker",
        "Период открутки",
        "Сегодня входит в период кампании" if start_ok and end_ok
        else ("Дата старта ещё не наступила" if not start_ok else "Дата окончания уже прошла"),
    )

    # 3. Общий бюджет
    total_budget = float(camp["total_budget"] or 0)
    spent_total = float(camp["spent_total"] or 0)
    budget_ok = total_budget <= 0 or spent_total < total_budget
    add(
        "total_budget",
        budget_ok,
        "blocker",
        "Общий бюджет",
        "Общий бюджет не исчерпан" if budget_ok
        else f"Израсходован весь общий бюджет ({spent_total:.2f} ₽ из {total_budget:.2f} ₽). Увеличьте бюджет.",
    )

    # 4. Дневной бюджет
    daily_budget = float(camp["daily_budget"] or 0)
    spent_today = float(camp["spent_today"] or 0)
    daily_ok = daily_budget <= 0 or spent_today < daily_budget
    add(
        "daily_budget",
        daily_ok,
        "warning",
        "Дневной бюджет",
        "Дневной бюджет не исчерпан" if daily_ok
        else f"Дневной бюджет на сегодня израсходован ({spent_today:.2f} ₽). Показы возобновятся после сброса в 00:00 МСК.",
    )

    # 5. Рекламодатель и баланс
    adv = await pool.fetchrow(
        "SELECT name, status, balance FROM advertisers WHERE id = $1", camp["advertiser_id"]
    )
    adv_active = adv is not None and adv["status"] == "active"
    balance = float(adv["balance"]) if adv and adv["balance"] is not None else 0.0
    add(
        "advertiser_status",
        adv_active,
        "blocker",
        "Рекламодатель",
        "Рекламодатель активен" if adv_active
        else "Рекламодатель не активен — активируйте его в разделе «Рекламодатели».",
    )
    add(
        "balance",
        balance > 0,
        "blocker",
        "Баланс",
        f"Баланс рекламодателя: {balance:.2f} ₽" if balance > 0
        else "Баланс рекламодателя 0 ₽ — торги остановлены. Пополните баланс в разделе «Финансы».",
    )

    # 6. Line items (группы объявлений)
    li_total = await pool.fetchval(
        "SELECT count(*) FROM line_items WHERE campaign_id = $1", campaign_id
    )
    li_active = await pool.fetchval(
        "SELECT count(*) FROM line_items WHERE campaign_id = $1 AND status = 'active' AND bid_price > 0",
        campaign_id,
    )
    add(
        "line_items",
        li_active > 0,
        "blocker",
        "Группы объявлений",
        f"Активных групп со ставкой > 0: {li_active}" if li_active > 0
        else (
            "Нет ни одной группы объявлений — создайте группу с таргетингом и ставкой."
            if li_total == 0
            else "Все группы на паузе или со ставкой 0 ₽. Активируйте группу и задайте ставку."
        ),
    )

    # 7. Одобренные креативы
    cr_total = await pool.fetchval(
        "SELECT count(*) FROM creatives WHERE campaign_id = $1", campaign_id
    )
    cr_approved = await pool.fetchval(
        "SELECT count(*) FROM creatives WHERE campaign_id = $1 AND status = 'approved'", campaign_id
    )
    cr_pending = await pool.fetchval(
        "SELECT count(*) FROM creatives WHERE campaign_id = $1 AND status = 'pending'", campaign_id
    )
    add(
        "creatives",
        cr_approved > 0,
        "blocker",
        "Одобренные креативы",
        f"Одобренных креативов: {cr_approved}" if cr_approved > 0
        else (
            "Нет ни одного креатива — загрузите креативы в кампанию."
            if cr_total == 0
            else f"Ни один креатив не одобрен ({cr_pending} на модерации). Дождитесь модерации."
        ),
    )

    # 8. Маркировка ОРД (erid) — блокер только для РФ-трафика
    targets_ru = await pool.fetchval(
        """SELECT bool_or(
                 COALESCE(jsonb_array_length(targeting->'geo_countries'), 0) = 0
                 OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(targeting->'geo_countries') g
                            WHERE upper(g) IN ('RU','RUS'))
               )
           FROM line_items WHERE campaign_id = $1""",
        campaign_id,
    )
    ru_relevant = targets_ru is None or targets_ru
    unmarked = 0
    if ru_relevant and cr_approved:
        unmarked = await pool.fetchval(
            "SELECT count(*) FROM creatives WHERE campaign_id = $1 AND status = 'approved' AND (erid IS NULL OR erid = '')",
            campaign_id,
        )
    add(
        "ord_marking",
        not (ru_relevant and unmarked),
        "blocker",
        "Маркировка ОРД (erid)",
        "Маркировка не требуется для выбранного гео" if not ru_relevant
        else ("Все одобренные креативы промаркированы" if not unmarked
              else f"{unmarked} одобренных креатив(ов) без erid. Зарегистрируйте их в ОРД (38-ФЗ)."),
    )

    # 9. Заблокированные оптимизатором источники (только предупреждение)
    blocked = await pool.fetchval(
        "SELECT count(*) FROM optimizer_blocks WHERE campaign_id = $1", campaign_id
    )
    add(
        "optimizer_blocks",
        True,
        "warning",
        "Заблокированные источники",
        "Оптимизатор не блокировал источники" if not blocked
        else f"Оптимизатор заблокировал {blocked} источник(ов) из-за низкого качества трафика. Это сужает охват.",
    )

    blockers = [c for c in checks if not c["ok"] and c["severity"] == "blocker"]
    warnings = [c for c in checks if not c["ok"] and c["severity"] == "warning"]
    if blockers:
        summary = "Кампания не откручивается: устраните блокирующие проблемы ниже."
        eligible = False
    elif warnings:
        summary = "Кампания участвует в торгах, но охват ограничен — см. предупреждения."
        eligible = True
    else:
        summary = "Все проверки пройдены — кампания готова к откруткам."
        eligible = True

    return {
        "eligible": eligible,
        "summary": summary,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "checks": checks,
    }


@router.get("/{campaign_id}/blocks")
async def list_optimizer_blocks(campaign_id: str, user: dict = Depends(current_user)):
    """Traffic sources blocked by the buying optimizer for this campaign."""
    pool = await get_pool()
    owned = await pool.fetchval(
        "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    rows = await pool.fetch(
        """SELECT b.*, sp.name AS partner_name
           FROM optimizer_blocks b
           LEFT JOIN supply_partners sp ON sp.id::text = b.block_value AND b.block_type = 'supply_partner'
           WHERE b.campaign_id = $1 ORDER BY b.created_at DESC""",
        campaign_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["id"], d["campaign_id"] = str(d["id"]), str(d["campaign_id"])
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


@router.delete("/{campaign_id}/blocks/{block_id}", status_code=204)
async def remove_optimizer_block(campaign_id: str, block_id: str, user: dict = Depends(require_campaign_editor)):
    """Manually unblock a source (the optimizer will not re-add it unless metrics degrade again)."""
    pool = await get_pool()
    deleted = await pool.fetchval(
        """DELETE FROM optimizer_blocks b USING campaigns c
           WHERE b.id = $1 AND b.campaign_id = $2 AND c.id = b.campaign_id AND c.user_id = $3
           RETURNING b.id""",
        block_id, campaign_id, user["id"],
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Блок не найден")


@router.get("/{campaign_id}/shading")
async def get_shading_factors(campaign_id: str, user: dict = Depends(current_user)):
    """Текущие факторы bid-шейдинга по SSP + оценка экономии за 7 дней."""
    pool = await get_pool()
    own = await pool.fetchval(
        "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if not own:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    factors = await pool.fetch(
        """
        SELECT f.supply_partner_id, sp.name AS partner_name, f.factor,
               f.bids, f.wins, f.avg_ratio, f.updated_at
        FROM bid_shading_factors f
        JOIN supply_partners sp ON sp.id = f.supply_partner_id
        WHERE f.campaign_id = $1
        ORDER BY sp.name
        """,
        campaign_id,
    )
    savings = await pool.fetchval(
        """
        SELECT COALESCE(sum(price - clear_price), 0)
        FROM bid_events
        WHERE event_type = 'win' AND campaign_id = $1
          AND clear_price IS NOT NULL AND price > clear_price
          AND created_at > now() - interval '7 days'
        """,
        campaign_id,
    )
    return {
        "factors": [dict(f) for f in factors],
        "savings_7d": float(savings or 0),
    }
