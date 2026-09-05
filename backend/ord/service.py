"""Сервисный слой ОРД: подбор договора, регистрация креатива, erid-гейт фида.

Используется из трёх мест, которые раньше расходились:
  * routers/moderation.py  — approve креатива → авторегистрация → erid;
  * routers/ord_router.py  — ручная регистрация с страницы «Маркировка»;
  * routers/bidder_feed.py и routers/rtb.py — фид биддера не отдаёт
    креатив без erid, если группа таргетирована на РФ (38-ФЗ).

Решение продукта (2026-09-05): маркировка — ответственность платформы,
а не биддера. Go-биддер erid не проверяет и не должен; статистика в ОРД
уходит из БД кроном, не из биддера.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .adapter import OrdError, get_ord_adapter

# ---------------------------------------------------------------- feed gate --
# Условие в WHERE фида: креатив допускается, если у него есть erid ИЛИ группа
# явно таргетирована только на не-РФ страны. Пустой geo_countries = «весь мир»
# = включает РФ = erid обязателен. Тот же критерий, что в diagnostics
# (campaigns.py, проверка ord_marking) — расходиться они не должны.
_RU_CODES_SQL = "('RU','RUS')"

ERID_FEED_GATE_SQL = f"""
    AND (
        (cr.erid IS NOT NULL AND cr.erid <> '')
        OR (
            COALESCE(jsonb_array_length(li.targeting->'geo_countries'), 0) > 0
            AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(li.targeting->'geo_countries') g
                WHERE upper(g) IN {_RU_CODES_SQL}
            )
        )
    )
"""


def feed_gate_enabled() -> bool:
    """ORD_FEED_GATE=off отключает erid-гейт фида (только dev/тест-стенды
    без ОРД). Согласовано с LAUNCH_GATE в campaigns.py."""
    return os.getenv("ORD_FEED_GATE", "on") != "off"


def feed_gate_sql() -> str:
    return ERID_FEED_GATE_SQL if feed_gate_enabled() else ""


async def campaign_targets_ru(pool, campaign_id: str) -> bool:
    """True, если хотя бы одна группа кампании показывается в РФ
    (явно RU или без ограничения по странам). Нет групп → True (консервативно)."""
    targets_ru = await pool.fetchval(
        f"""SELECT bool_or(
                 COALESCE(jsonb_array_length(targeting->'geo_countries'), 0) = 0
                 OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(targeting->'geo_countries') g
                            WHERE upper(g) IN {_RU_CODES_SQL})
               )
           FROM line_items WHERE campaign_id = $1""",
        campaign_id,
    )
    return targets_ru is None or bool(targets_ru)


# ----------------------------------------------------------- contract lookup --
async def resolve_contract(pool, creative: dict) -> dict | None:
    """Договор для регистрации креатива.

    1. Явно выбранный creatives.contract_id — если он зарегистрирован в ОРД.
    2. Иначе единственный зарегистрированный договор владельца, где заказчик
       (client_id) — контрагент креатива. Если подходящих несколько — None:
       выбирать за пользователя нельзя, это юридический документ.
    """
    if creative.get("contract_id"):
        row = await pool.fetchrow(
            """SELECT * FROM ord_contracts
               WHERE id = $1 AND user_id = $2 AND ord_status = 'registered'""",
            creative["contract_id"], creative["user_id"],
        )
        if row:
            return dict(row)
    if not creative.get("counterparty_id"):
        return None
    rows = await pool.fetch(
        """SELECT * FROM ord_contracts
           WHERE user_id = $1 AND client_id = $2 AND ord_status = 'registered'
           ORDER BY signed_at DESC, created_at DESC LIMIT 2""",
        creative["user_id"], creative["counterparty_id"],
    )
    return dict(rows[0]) if len(rows) == 1 else None


async def _kktu_for(pool, creative: dict) -> str | None:
    if creative.get("kktu_code"):
        return creative["kktu_code"]
    camp = await pool.fetchrow(
        "SELECT kktu_code FROM campaigns WHERE id = $1", creative["campaign_id"]
    )
    return camp["kktu_code"] if camp and camp["kktu_code"] else None


def _payload(creative: dict, contract: dict, kktu: str | None) -> dict[str, Any]:
    # ОРД регистрирует конкретное содержимое: медиа (URL файлов) и тексты.
    # erid выдаётся на него, поэтому смена картинки/текста = новая регистрация
    # (см. routers/creatives.py). Без этих полей перерегистрация была бы фикцией.
    media = [u for u in (
        creative.get("asset_url"), creative.get("video_url"), creative.get("native_icon_url"),
    ) if u]
    texts = [t for t in (
        creative.get("native_title"), creative.get("native_description"), creative.get("native_cta"),
    ) if t]
    return {
        "contract_external_id": contract["ord_external_id"],
        "type": creative["type"],
        "name": creative["name"],
        "click_url": creative.get("click_url"),
        "description": creative.get("native_description") or creative["name"],
        "media_urls": media,
        "texts": texts,
        "html_snippet": creative.get("html_snippet"),
        "vast_xml": creative.get("vast_xml"),
        # Тематика ККТУ: категория товара/услуги для маркировки объявления
        "kktu": kktu,
    }


async def _mark(pool, creative_id: str, contract_id: str | None, status: str,
                payload: dict | None, error: str | None) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO ord_creatives (creative_id, contract_id, status, payload, error)
           VALUES ($1,$2,$3,$4,$5)
           ON CONFLICT (creative_id) DO UPDATE
             SET status = EXCLUDED.status, error = EXCLUDED.error,
                 contract_id = COALESCE(EXCLUDED.contract_id, ord_creatives.contract_id),
                 payload = COALESCE(EXCLUDED.payload, ord_creatives.payload),
                 updated_at = now()
           RETURNING *""",
        creative_id, contract_id, status,
        json.dumps(payload, ensure_ascii=False) if payload else None, error,
    )
    return dict(row)


def _result(outcome: str, record: dict, error: str | None = None) -> dict:
    """Результат регистрации: outcome + строка ord_creatives.

    outcome: ok                → record['erid'] заполнен;
             awaiting_contract → договор не найден/не зарегистрирован, ошибки ОРД нет;
             failed            → ОРД вернул ошибку (error).
    'status' в record — статус строки ord_creatives (registered/awaiting_contract/failed),
    он намеренно не смешивается с outcome.
    """
    out = dict(record)
    out["outcome"] = outcome
    out["error"] = error if error is not None else record.get("error")
    return out


async def register_creative(pool, creative: dict, *, contract_id: str | None = None) -> dict:
    """Регистрирует креатив в ОРД и записывает erid в creatives.erid.

    Идемпотентно: уже зарегистрированный креатив возвращается как есть.
    Не бросает исключений наверх — результат всегда со статусом outcome,
    чтобы approve модератора не ломался из-за недоступности ОРД.
    """
    creative = dict(creative)
    existing = await pool.fetchrow(
        "SELECT * FROM ord_creatives WHERE creative_id = $1", creative["id"]
    )
    if existing and existing["status"] == "registered" and existing["erid"]:
        return _result("ok", dict(existing))

    if contract_id:
        creative["contract_id"] = contract_id
    contract = await resolve_contract(pool, creative)
    if not contract:
        why = (
            "Нет зарегистрированного в ОРД договора для контрагента креатива. "
            "Зарегистрируйте договор в разделе «Маркировка (ОРД)» или выберите его на шаге «Маркировка» креатива."
            if creative.get("counterparty_id")
            else "К креативу не привязан контрагент (рекламодатель) — выберите его на шаге «Маркировка»."
        )
        rec = await _mark(pool, creative["id"], creative.get("contract_id"), "awaiting_contract", None, why)
        return _result("awaiting_contract", rec, why)

    kktu = await _kktu_for(pool, creative)
    payload = _payload(creative, contract, kktu)
    adapter = await get_ord_adapter()
    try:
        out = await adapter.register_creative(payload)
    except OrdError as e:
        rec = await _mark(pool, creative["id"], contract["id"], "failed", payload, str(e))
        return _result("failed", rec, str(e))

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO ord_creatives
                     (creative_id, contract_id, erid, ord_provider, ord_external_id,
                      status, payload, registered_at, error)
                   VALUES ($1,$2,$3,$4,$5,'registered',$6, now(), NULL)
                   ON CONFLICT (creative_id) DO UPDATE
                     SET erid=EXCLUDED.erid, ord_external_id=EXCLUDED.ord_external_id,
                         contract_id=EXCLUDED.contract_id, ord_provider=EXCLUDED.ord_provider,
                         status='registered', payload=EXCLUDED.payload,
                         error=NULL, registered_at=now(), updated_at=now()
                   RETURNING *""",
                creative["id"], contract["id"], out["erid"], adapter.provider,
                out.get("external_id"), json.dumps(payload, ensure_ascii=False),
            )
            await conn.execute(
                """UPDATE creatives SET erid = $2, contract_id = $3, updated_at = now()
                   WHERE id = $1""",
                creative["id"], out["erid"], contract["id"],
            )
    return _result("ok", dict(row))
