"""SSP-партнёры: онбординг, управление токеном и автопроверка подключения.

Создание/изменение/удаление — только superadmin/admin (require_admin):
подключение SSP — платформенная операция уровня администратора.
Чтение — любой платформенный пользователь (страница /supply).
"""

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from rbac import require_admin
from db import get_pool
from schemas import SupplyPartnerIn, SupplyPartnerPatch
import ssp_check
import ssp_tokens

router = APIRouter(prefix="/supply-partners", tags=["supply-partners"])

_JSONB_FIELDS = {"allowed_ad_types", "ip_whitelist"}


def _serialize(row) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"])
    d["bid_floor"] = float(d["bid_floor"])
    for k in ("allowed_ad_types", "ip_whitelist", "last_check"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    # В БД только хэш (миграция 032); наружу — признак и хвост для сверки
    token_hash = d.pop("auth_token_hash", None)
    hint = d.pop("auth_token_hint", None)
    d.pop("auth_token", None)  # на случай прогона до миграции 032
    d["has_auth_token"] = bool(token_hash)
    d["auth_token_hint"] = f"…{hint}" if token_hash and hint else None
    return d


@router.get("")
async def list_partners(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM supply_partners WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
    )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_partner(body: SupplyPartnerIn, user: dict = Depends(require_admin)):
    pool = await get_pool()
    endpoint_key = secrets.token_urlsafe(16)
    row = await pool.fetchrow(
        """
        INSERT INTO supply_partners
            (user_id, name, endpoint_key, auction_type, bid_floor, qps_limit, allowed_ad_types, notes,
             protocol_version, currency, tmax_ms, ip_whitelist, seat_id, contact_email, status)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12::jsonb,$13,$14,'draft') RETURNING *
        """,
        user["id"], body.name, endpoint_key, body.auction_type, body.bid_floor,
        body.qps_limit, json.dumps(body.allowed_ad_types), body.notes,
        body.protocol_version, body.currency, body.tmax_ms,
        json.dumps(body.ip_whitelist), body.seat_id, body.contact_email,
    )
    return _serialize(row)


@router.patch("/{partner_id}")
async def update_partner(partner_id: str, body: SupplyPartnerPatch, user: dict = Depends(require_admin)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    pool = await get_pool()
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        if k in _JSONB_FIELDS:
            sets.append(f"{k} = ${i}::jsonb")
            values.append(json.dumps(v))
        else:
            sets.append(f"{k} = ${i}")
            values.append(v)
    row = await pool.fetchrow(
        f"UPDATE supply_partners SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        partner_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    return _serialize(row)


@router.delete("/{partner_id}", status_code=204)
async def delete_partner(partner_id: str, user: dict = Depends(require_admin)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM supply_partners WHERE id = $1 AND user_id = $2", partner_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")


# --- Auth-токен ----------------------------------------------------------------


@router.post("/{partner_id}/token")
async def rotate_token(partner_id: str, user: dict = Depends(require_admin)):
    """Генерирует (или перегенерирует) shared-secret токен партнёра.

    Полное значение токена возвращается ТОЛЬКО из этого ответа — сохраните
    и передайте SSP. Дальше API отдаёт только хвост (auth_token_hint).
    """
    pool = await get_pool()
    token = ssp_tokens.generate_token()
    row = await pool.fetchrow(
        """UPDATE supply_partners
              SET auth_token_hash = $3, auth_token_hint = $4, updated_at = now()
            WHERE id = $1 AND user_id = $2 RETURNING id""",
        partner_id, user["id"], ssp_tokens.hash_token(token), ssp_tokens.token_hint(token),
    )
    if not row:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    return {"token": token, "header": ssp_tokens.HEADER_NAME}


@router.delete("/{partner_id}/token", status_code=204)
async def revoke_token(partner_id: str, user: dict = Depends(require_admin)):
    pool = await get_pool()
    result = await pool.execute(
        """UPDATE supply_partners
              SET auth_token_hash = NULL, auth_token_hint = NULL, updated_at = now()
            WHERE id = $1 AND user_id = $2""",
        partner_id, user["id"],
    )
    if result.endswith("0"):
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")


# --- Автопроверка подключения ----------------------------------------------------


@router.post("/{partner_id}/check")
async def connection_check(partner_id: str, user: dict = Depends(require_admin)):
    """Прогоняет интеграционный чек-лист подключения и сохраняет результат.

    Партнёр в статусе draft на время проверки допускается к биддеру
    (process_bid_request принимает active/testing; draft переводим в testing
    автоматически при первом запуске проверки — это и есть смысл теста).
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM supply_partners WHERE id = $1 AND user_id = $2", partner_id, user["id"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    partner = dict(row)
    for k in ("allowed_ad_types", "ip_whitelist"):
        if isinstance(partner.get(k), str):
            partner[k] = json.loads(partner[k])

    # draft -> testing: endpoint начинает принимать запросы
    if partner["status"] == "draft":
        await pool.execute(
            "UPDATE supply_partners SET status = 'testing', updated_at = now() WHERE id = $1", partner_id
        )
        partner["status"] = "testing"

    from routers.rtb import process_bid_request

    result = await ssp_check.run_connection_check(partner, process_bid_request)
    await pool.execute(
        "UPDATE supply_partners SET last_check = $2::jsonb, updated_at = now() WHERE id = $1",
        partner_id, json.dumps(result),
    )
    return result
