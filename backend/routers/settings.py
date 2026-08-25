"""Настройки аккаунта: валюта/наценка на курс, глобальные white/blacklist площадок.

app_settings — key/value (jsonb) на пользователя; domain_lists — глобальные
листы доменов, применяются биддером ко всем кампаниям аккаунта. Кнопка
«в блэклист» из статистики бьёт в POST /settings/domain-lists с source=stats_click.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import fx as fx_mod
from auth import current_user
from db import get_pool

router = APIRouter(prefix="/settings", tags=["settings"])

DEFAULTS: dict = {
    "currency": "RUB",           # кабинет всегда в рублях
    "fx_markup_percent": 3.0,    # наценка к курсу ЦБ на волатильность
}


async def _load_settings(user_id: str) -> dict:
    pool = await get_pool()
    rows = await pool.fetch("SELECT key, value FROM app_settings WHERE user_id = $1", user_id)
    out = dict(DEFAULTS)
    for r in rows:
        v = r["value"]
        out[r["key"]] = json.loads(v) if isinstance(v, str) else v
    return out


@router.get("")
async def get_settings(user: dict = Depends(current_user)):
    return await _load_settings(user["id"])


class SettingsPatch(BaseModel):
    fx_markup_percent: float | None = Field(default=None, ge=0, le=50)


@router.patch("")
async def patch_settings(body: SettingsPatch, user: dict = Depends(current_user)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "Нет полей для обновления")
    pool = await get_pool()
    for k, v in updates.items():
        await pool.execute(
            """INSERT INTO app_settings (user_id, key, value) VALUES ($1, $2, $3::jsonb)
               ON CONFLICT (user_id, key)
                 DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
            user["id"], k, json.dumps(v),
        )
    return await _load_settings(user["id"])


@router.get("/fx")
async def fx_rate(currency: str = "USD", user: dict = Depends(current_user)):
    """Курс ЦБ + наценка аккаунта = effective_rate (по нему конвертируем $-закупку)."""
    rate = await fx_mod.get_rate(currency)
    if rate is None:
        raise HTTPException(503, "Курс недоступен — попробуйте позже")
    s = await _load_settings(user["id"])
    markup = float(s.get("fx_markup_percent") or 0)
    return {
        "currency": currency,
        "cbr_rate": round(rate, 4),
        "markup_percent": markup,
        "effective_rate": round(rate * (1 + markup / 100), 4),
    }


# --- Глобальные white/blacklist площадок -------------------------------------
class DomainIn(BaseModel):
    domain: str = Field(min_length=2, max_length=255)
    list_type: str = Field(default="black", pattern="^(black|white)$")
    source: str = Field(default="manual", pattern="^(manual|stats_click|rule)$")
    note: str | None = Field(default=None, max_length=300)


@router.get("/domain-lists")
async def list_domains(list_type: str | None = None, user: dict = Depends(current_user)):
    pool = await get_pool()
    if list_type:
        rows = await pool.fetch(
            "SELECT * FROM domain_lists WHERE user_id = $1 AND list_type = $2 ORDER BY created_at DESC",
            user["id"], list_type,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM domain_lists WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
        )
    return [dict(r) | {"id": str(r["id"]), "user_id": str(r["user_id"]),
                       "created_at": r["created_at"].isoformat()} for r in rows]


@router.post("/domain-lists", status_code=201)
async def add_domain(body: DomainIn, user: dict = Depends(current_user)):
    domain = body.domain.strip().lower()
    if "/" in domain or " " in domain:
        raise HTTPException(422, "Укажите домен без протокола и пути, например example.com")
    pool = await get_pool()
    row = await pool.fetchrow(
        """INSERT INTO domain_lists (user_id, domain, list_type, source, note)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (user_id, domain, list_type) DO UPDATE SET note = COALESCE(EXCLUDED.note, domain_lists.note)
           RETURNING *""",
        user["id"], domain, body.list_type, body.source, body.note,
    )
    return dict(row) | {"id": str(row["id"]), "user_id": str(row["user_id"]),
                        "created_at": row["created_at"].isoformat()}


@router.delete("/domain-lists/{item_id}", status_code=204)
async def remove_domain(item_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM domain_lists WHERE id = $1 AND user_id = $2", item_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(404, "Запись не найдена")
