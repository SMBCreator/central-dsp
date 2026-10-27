"""ТЗ п.5: рынок рекламодателя — 'ru' (РФ) или 'foreign' (только за пределами РФ).

Для 'foreign' маркировка (38-ФЗ) не нужна, поэтому:
  * российские гео запрещены — UI их скрывает, а здесь вторая линия защиты (422);
  * ОРД-гейты запуска и фида не меняются: они уже не применяются к группам
    только на зарубежные места (ord.service._not_ru_only_sql). Этот модуль
    использует ту же функцию, а не дублирует критерий «не РФ»;
  * объекты рекламодателя исключаются из ОРД-выгрузок и раздела «Маркировка»
    (foreign_campaign_sql / foreign_counterparty_sql).

Смена рынка после создания — только superadmin и только пока у рекламодателя
нет сущностей, уже отправленных в ОРД (иначе в реестре останутся записи без
продолжения). Каждая смена пишется в audit_log.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from ord.service import _not_ru_only_sql

AD_MARKETS = ("ru", "foreign")

FOREIGN_GEO_ERROR = (
    "Рекламодатель работает «за пределами РФ»: российские места (РФ, её регионы и города, "
    "показы без определённой страны) в гео недоступны. Выберите зарубежные страны или города."
)
FOREIGN_NO_ORD = "Рекламодатель работает «за пределами РФ» — маркировка в ОРД для него не требуется."


def foreign_campaign_sql(campaign_col: str) -> str:
    """Предикат «кампания принадлежит рекламодателю 'foreign'». NULL-кампания — false."""
    return (
        "EXISTS (SELECT 1 FROM campaigns fc JOIN advertisers fa ON fa.id = fc.advertiser_id "
        f"WHERE fc.id = {campaign_col} AND fa.ad_market = 'foreign')"
    )


def foreign_counterparty_sql(counterparty_col: str) -> str:
    """Предикат «юрлицо ОРД привязано к рекламодателю 'foreign'»."""
    return (
        "EXISTS (SELECT 1 FROM counterparties fcp JOIN advertisers fa ON fa.id = fcp.advertiser_id "
        f"WHERE fcp.id = {counterparty_col} AND fa.ad_market = 'foreign')"
    )


async def campaign_ad_market(conn, campaign_id: str) -> str:
    market = await conn.fetchval(
        "SELECT a.ad_market FROM campaigns c JOIN advertisers a ON a.id = c.advertiser_id WHERE c.id = $1",
        campaign_id,
    )
    return market or "ru"


async def targeting_not_ru_only(conn, targeting: dict[str, Any] | None) -> bool:
    """Тот же критерий, что erid-гейт фида: группа гарантированно не показывается в РФ."""
    return bool(await conn.fetchval(f"SELECT {_not_ru_only_sql('$1::jsonb')}", json.dumps(targeting or {})))


async def assert_targeting_allowed(conn, campaign_id: str, targeting: dict[str, Any] | None) -> None:
    """422, если кампания рекламодателя 'foreign' получает таргетинг, включающий РФ."""
    if await campaign_ad_market(conn, campaign_id) != "foreign":
        return
    if not await targeting_not_ru_only(conn, targeting):
        raise HTTPException(status_code=422, detail=FOREIGN_GEO_ERROR)


async def campaign_has_ru_geo(conn, campaign_id: str) -> bool:
    """Есть ли у кампании группа, которая может показываться в РФ. Нет групп — нет РФ-гео."""
    return bool(await conn.fetchval(
        f"SELECT COALESCE(bool_or(NOT {_not_ru_only_sql('targeting')}), false) FROM line_items WHERE campaign_id = $1",
        campaign_id,
    ))


async def assert_campaign_geo_allowed(conn, campaign_id: str) -> None:
    """Активация кампании рекламодателя 'foreign' с РФ-гео (запрос в обход UI) — 422."""
    if await campaign_ad_market(conn, campaign_id) != "foreign":
        return
    if await campaign_has_ru_geo(conn, campaign_id):
        raise HTTPException(status_code=422, detail=FOREIGN_GEO_ERROR)


async def assert_campaign_not_foreign(conn, campaign_id: str | None) -> None:
    """Операции ОРД (подтверждение цепочки, ручная регистрация креатива) для 'foreign' — 409."""
    if campaign_id and await campaign_ad_market(conn, str(campaign_id)) == "foreign":
        raise HTTPException(status_code=409, detail=FOREIGN_NO_ORD)


# ------------------------------------------------------------ смена рынка --
_SENT_TO_ORD_SQL = """
SELECT
  (SELECT count(*) FROM counterparties cp
    WHERE cp.advertiser_id = $1
      AND (cp.ord_external_id IS NOT NULL OR cp.ord_status IN ('registered', 'sending', 'erir_error')))
  +
  (SELECT count(*) FROM ord_contracts oc
    WHERE (oc.ord_external_id IS NOT NULL OR oc.ord_status IN ('registered', 'sending', 'erir_error'))
      AND (
        oc.client_id IN (SELECT id FROM counterparties WHERE advertiser_id = $1)
        OR oc.id IN (SELECT initial_contract_id FROM campaigns WHERE advertiser_id = $1 AND initial_contract_id IS NOT NULL)
        OR oc.id IN (SELECT executive_contract_id FROM campaigns WHERE advertiser_id = $1 AND executive_contract_id IS NOT NULL)
      ))
  +
  (SELECT count(*) FROM ord_creatives o
     JOIN creatives cr ON cr.id = o.creative_id
     JOIN campaigns c ON c.id = cr.campaign_id
    WHERE c.advertiser_id = $1
      AND (o.erid IS NOT NULL OR o.ord_external_id IS NOT NULL
           OR o.status IN ('registered', 'sending', 'erir_error', 'superseded')))
"""


async def ord_entities_count(conn, advertiser_id: str) -> int:
    """Сколько сущностей рекламодателя уже отправлено в ОРД (контрагенты, договоры, креативы)."""
    return int(await conn.fetchval(_SENT_TO_ORD_SQL, advertiser_id) or 0)


async def advertiser_has_ru_geo(conn, advertiser_id: str) -> bool:
    return bool(await conn.fetchval(
        f"""SELECT COALESCE(bool_or(NOT {_not_ru_only_sql('li.targeting')}), false)
              FROM line_items li JOIN campaigns c ON c.id = li.campaign_id
             WHERE c.advertiser_id = $1""",
        advertiser_id,
    ))


async def change_ad_market(conn, advertiser: dict, new_market: str, user: dict) -> None:
    """Проверки и аудит смены рынка. Вызывающий сам выполняет UPDATE в той же транзакции."""
    old = advertiser.get("ad_market") or "ru"
    if new_market == old:
        return
    if user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Менять, где планируется реклама, может только суперадминистратор")
    adv_id = str(advertiser["id"])
    if new_market == "foreign":
        sent = await ord_entities_count(conn, adv_id)
        if sent:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"У рекламодателя {sent} сущност(ей) уже в ОРД (контрагенты, договоры или креативы). "
                    "Перевод «за пределы РФ» оставит их в реестре без продолжения, а выгрузки перестанут "
                    "их учитывать. Создайте для зарубежной рекламы отдельного рекламодателя."
                ),
            )
    elif await advertiser_has_ru_geo(conn, adv_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "У рекламодателя есть группы с российским гео, созданные в обход ограничений режима "
                "«за пределами РФ». Исправьте их гео, затем повторите перевод."
            ),
        )
    await conn.execute(
        """INSERT INTO audit_log (user_id, user_email, user_role, method, path, status_code, entity, entity_id, payload)
           VALUES ($1, $2, $3, 'PATCH', $4, 200, 'advertisers', $5, $6::jsonb)""",
        user.get("id"), user.get("email"), user.get("role"),
        f"/advertisers/{adv_id}/ad_market", adv_id,
        json.dumps({"field": "ad_market", "from": old, "to": new_market}),
    )
