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

from .adapter import MOCK_PROVIDER, OrdError, get_ord_adapter, mock_allowed, mock_erid_accepted

# ---------------------------------------------------------------- feed gate --
# Условие в WHERE фида: креатив допускается, если у него есть erid ИЛИ группа
# явно таргетирована только на не-РФ страны. Пустой geo_countries = «весь мир»
# = включает РФ = erid обязателен. Тот же критерий, что в diagnostics
# (campaigns.py, проверка ord_marking) — расходиться они не должны.
_RU_CODES_SQL = "('RU','RUS')"

_NOT_RU_ONLY_SQL = f"""
            COALESCE(jsonb_array_length(li.targeting->'geo_countries'), 0) > 0
            AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(li.targeting->'geo_countries') g
                WHERE upper(g) IN {_RU_CODES_SQL}
            )
"""

ERID_FEED_GATE_SQL = f"""
    AND (
        (cr.erid IS NOT NULL AND cr.erid <> '')
        OR (
{_NOT_RU_ONLY_SQL}
        )
    )
"""

# Audit L-01: в production erid, выданный mock-адаптером, — не erid. Строка
# ord_creatives.ord_provider = 'mock' — источник истины; префикс 'MOCK-' —
# страховка для erid, проставленных руками/импортом без строки ord_creatives.
ERID_FEED_GATE_NO_MOCK_SQL = f"""
    AND (
        (
            cr.erid IS NOT NULL AND cr.erid <> ''
            AND cr.erid NOT LIKE 'MOCK-%'
            AND NOT EXISTS (
                SELECT 1 FROM ord_creatives oc
                WHERE oc.creative_id = cr.id AND oc.ord_provider = '{MOCK_PROVIDER}'
            )
        )
        OR (
{_NOT_RU_ONLY_SQL}
        )
    )
"""


def unmarked_creative_sql(alias: str = "creatives") -> str:
    """WHERE-предикат «у креатива нет пригодного erid» — для диагностики и
    launch gate кампании. Тот же критерий, что в feed_gate_sql(): в production
    mock-erid считается отсутствующим."""
    base = f"({alias}.erid IS NULL OR {alias}.erid = '')"
    if mock_erid_accepted():
        return base
    return (
        f"({base} OR {alias}.erid LIKE 'MOCK-%' OR EXISTS ("
        f"SELECT 1 FROM ord_creatives oc WHERE oc.creative_id = {alias}.id AND oc.ord_provider = '{MOCK_PROVIDER}'))"
    )


def feed_gate_enabled() -> bool:
    """ORD_FEED_GATE=off отключает erid-гейт фида — только dev/тест-стенды
    без ОРД; в production флаг игнорируется (startup_checks его ещё и
    подсвечивает). Согласовано с LAUNCH_GATE в campaigns.py."""
    if not mock_allowed():
        return True
    return os.getenv("ORD_FEED_GATE", "on") != "off"


def feed_gate_sql() -> str:
    if not feed_gate_enabled():
        return ""
    return ERID_FEED_GATE_SQL if mock_erid_accepted() else ERID_FEED_GATE_NO_MOCK_SQL


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
CHAIN_NOT_CONFIRMED = (
    "Цепочка ОРД кампании не подтверждена. Откройте кампанию → «Данные ОРД», проверьте "
    "рекламодателя и договоры и нажмите «Подтвердить данные»."
)


async def campaign_chain(pool, creative: dict) -> dict | None:
    """Кампания креатива, если у неё задана цепочка ОРД (ord_spec §7 D2).
    NULL ord_mode = кампания до B13d, контрагент/договор берутся с креатива."""
    if not creative.get("campaign_id"):
        return None
    camp = await pool.fetchrow(
        """SELECT id, user_id, ord_mode, counterparty_id, initial_contract_id,
                  executive_contract_id, ord_confirmed_at
             FROM campaigns WHERE id = $1""",
        creative["campaign_id"],
    )
    return dict(camp) if camp and camp["ord_mode"] else None


async def resolve_contract(pool, creative: dict) -> dict | None:
    """Договор для регистрации креатива.

    0. Цепочка кампании (B13d): подтверждённая — изначальный договор
       (в режиме self — исполнительский); контрагент креатива = рекламодатель
       кампании (снапшот). Неподтверждённая — None.
    1. Явно выбранный creatives.contract_id — если он зарегистрирован в ОРД.
    2. Иначе единственный зарегистрированный договор владельца, где заказчик
       (client_id) — контрагент креатива. Если подходящих несколько — None:
       выбирать за пользователя нельзя, это юридический документ.
    """
    from ord.chain import load_chain, registration_contract

    camp = await campaign_chain(pool, creative)
    if camp:
        if not camp["ord_confirmed_at"]:
            return None
        chain = await load_chain(pool, camp)
        objs = chain["_objects"]
        contract = registration_contract(camp, objs["initial"], objs["executive"])
        if not contract or contract.get("ord_status") != "registered":
            return None
        creative["counterparty_id"] = str(camp["counterparty_id"])
        return contract
    if creative.get("contract_id"):
        row = await pool.fetchrow(
            """SELECT * FROM ord_contracts
               WHERE id = $1 AND user_id = $2 AND ord_status IN ('registered', 'erir_error')""",
            creative["contract_id"], creative["user_id"],
        )
        if row:
            return dict(row)
    if not creative.get("counterparty_id"):
        return None
    rows = await pool.fetch(
        """SELECT * FROM ord_contracts
           WHERE user_id = $1 AND client_id = $2 AND ord_status IN ('registered', 'erir_error')
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


def external_creative_id(creative_id: str, version: int) -> str:
    """externalCreativeId для ОРД: одна версия содержимого = один erid."""
    return f"{creative_id}:v{version}"


def external_statistic_id(creative_id: str, version: int, period: str) -> str:
    """externalStatisticId: повтор за тот же период = upsert (коррекция), не дубль."""
    return f"{creative_id}:v{version}:{period}"


async def current_version(pool, creative_id: str):
    """Открытая (superseded_at IS NULL) строка ord_creatives или None."""
    return await pool.fetchrow(
        "SELECT * FROM ord_creatives WHERE creative_id = $1 AND superseded_at IS NULL", creative_id
    )


async def supersede_current(pool, creative_id: str) -> bool:
    """Закрывает текущую зарегистрированную версию: erid остаётся в истории,
    статистика за часы до superseded_at относится к ней. Незарегистрированные
    (awaiting_*/failed) строки не закрываются — их просто перепробуют."""
    tag = await pool.execute(
        """UPDATE ord_creatives
              SET status = 'superseded', superseded_at = now(), updated_at = now()
            WHERE creative_id = $1 AND superseded_at IS NULL AND status IN ('registered', 'erir_error')""",
        creative_id,
    )
    return tag.endswith(" 1")


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


async def _next_version(conn, creative_id: str) -> int:
    return await conn.fetchval(
        "SELECT COALESCE(max(version), 0) + 1 FROM ord_creatives WHERE creative_id = $1", creative_id
    )


async def _mark(pool, creative_id: str, contract_id: str | None, status: str,
                payload: dict | None, error: str | None) -> dict:
    """Промежуточный статус (awaiting_*/failed) текущей версии.

    Открытая версия обновляется на месте; если открытой нет (первая регистрация
    или предыдущая закрыта supersede_current) — создаётся новая версия.
    """
    async with pool.acquire() as conn, conn.transaction():
        cur = await conn.fetchrow(
            "SELECT id, version FROM ord_creatives WHERE creative_id = $1 AND superseded_at IS NULL FOR UPDATE",
            creative_id,
        )
        payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
        if cur:
            row = await conn.fetchrow(
                """UPDATE ord_creatives
                      SET status = $2, error = $3,
                          contract_id = COALESCE($4, contract_id),
                          payload = COALESCE($5::jsonb, payload),
                          updated_at = now()
                    WHERE id = $1 RETURNING *""",
                cur["id"], status, error, contract_id, payload_json,
            )
        else:
            row = await conn.fetchrow(
                """INSERT INTO ord_creatives (creative_id, version, contract_id, status, payload, error)
                   VALUES ($1,$2,$3,$4,$5::jsonb,$6) RETURNING *""",
                creative_id, await _next_version(conn, creative_id), contract_id, status, payload_json, error,
            )
    return dict(row)


def _result(outcome: str, record: dict, error: str | None = None) -> dict:
    """Результат регистрации: outcome + строка ord_creatives.

    outcome: ok                → record['erid'] заполнен;
             awaiting_contract → договор не найден/не зарегистрирован, ошибки ОРД нет;
             awaiting_kktu     → нет кода ККТУ ни у креатива, ни у кампании, в ОРД не ходили;
             failed            → ОРД вернул ошибку (error).
    'status' в record — статус строки ord_creatives (registered/awaiting_contract/awaiting_kktu/failed),
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
    existing = await current_version(pool, creative["id"])
    if existing and existing["status"] == "registered" and existing["erid"]:
        return _result("ok", dict(existing))

    if contract_id:
        creative["contract_id"] = contract_id
    contract = await resolve_contract(pool, creative)
    if not contract:
        camp = await campaign_chain(pool, creative)
        if camp and not camp["ord_confirmed_at"]:
            why = CHAIN_NOT_CONFIRMED
        elif camp:
            why = ("Договор цепочки ОРД кампании не зарегистрирован в ОРД. "
                   "Зарегистрируйте его в разделе «Маркировка (ОРД)».")
        else:
            why = (
                "Нет зарегистрированного в ОРД договора для контрагента креатива. "
                "Зарегистрируйте договор в разделе «Маркировка (ОРД)» или выберите его на шаге «Маркировка» креатива."
                if creative.get("counterparty_id")
                else "К креативу не привязан контрагент (рекламодатель) — выберите его на шаге «Маркировка»."
            )
        rec = await _mark(pool, creative["id"], creative.get("contract_id"), "awaiting_contract", None, why)
        return _result("awaiting_contract", rec, why)

    # ККТУ обязателен при регистрации креатива в ОРД с 01.09.2023. Реальный ОРД
    # отклонит заявку без кода; мок — нет, и erid оказался бы выдан на
    # объявление без тематики. Проверяем до отправки, единообразно для всех
    # адаптеров, чтобы владелец получил понятную причину, а не ошибку ОРД.
    kktu = await _kktu_for(pool, creative)
    if not kktu:
        why = (
            "Не указан код ККТУ (тематика товара/услуги). Заполните его на шаге «Маркировка» "
            "креатива или в настройках кампании — без ККТУ ОРД не выдаёт erid."
        )
        rec = await _mark(pool, creative["id"], contract["id"], "awaiting_kktu", None, why)
        return _result("awaiting_kktu", rec, why)

    # Номер версии фиксируем до похода в ОРД: он входит в externalCreativeId.
    version = int(existing["version"]) if existing else await _next_version(pool, creative["id"])
    payload = _payload(creative, contract, kktu)
    payload["external_creative_id"] = external_creative_id(str(creative["id"]), version)
    payload["version"] = version
    try:
        # OrdNotConfigured (production без ключа) — тоже OrdError: креатив
        # уходит в failed с понятной причиной, approve модератора не ломается.
        adapter = await get_ord_adapter()
        out = await adapter.register_creative(payload)
    except OrdError as e:
        rec = await _mark(pool, creative["id"], contract["id"], "failed", payload, str(e))
        return _result("failed", rec, str(e))

    async with pool.acquire() as conn, conn.transaction():
        # Открытая версия (awaiting_*/failed) становится registered; закрытой
        # версии здесь быть не может — supersede_current выставил superseded_at,
        # и частичный уникальный индекс ux_ord_creatives_current это гарантирует.
        row = await conn.fetchrow(
            """INSERT INTO ord_creatives
                 (creative_id, version, contract_id, erid, ord_provider, ord_external_id,
                  status, payload, registered_at, error)
               VALUES ($1,$2,$3,$4,$5,$6,'registered',$7::jsonb, now(), NULL)
               ON CONFLICT (creative_id, version) DO UPDATE
                 SET erid=EXCLUDED.erid, ord_external_id=EXCLUDED.ord_external_id,
                     contract_id=EXCLUDED.contract_id, ord_provider=EXCLUDED.ord_provider,
                     status='registered', payload=EXCLUDED.payload,
                     error=NULL, registered_at=now(), updated_at=now()
               RETURNING *""",
            creative["id"], version, contract["id"], out["erid"], adapter.provider,
            out.get("external_id"), json.dumps(payload, ensure_ascii=False),
        )
        await conn.execute(
            """UPDATE creatives
                  SET erid = $2, contract_id = $3,
                      counterparty_id = COALESCE($4::uuid, counterparty_id),
                      ord_locked_at = COALESCE(ord_locked_at, now()),
                      updated_at = now()
                WHERE id = $1""",
            creative["id"], out["erid"], contract["id"], creative.get("counterparty_id"),
        )
        # B13g: повторная регистрация версии закрывает её открытые ошибки ЕРИР
        from .notifications import resolve_for

        await resolve_for(conn, "creative", f"{creative['id']}:v{version}")
    return _result("ok", dict(row))
