"""Слой атрибуции конверсий (audit F-02 / B14c): запись, покрытие, доверие к KPI.

conversions — факт; conversion_attributions — 1:1 «как привязано» (см. migrations/045).
Единственная точка, откуда отчёты, rules_engine и optimizer узнают, можно ли
верить conversion-KPI: `kpi_trustworthy_for_owner`.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import obs_metrics
from stats_math import coverage_ratio

STATUS_ATTRIBUTED = "attributed"
STATUS_OUT_OF_WINDOW = "out_of_window"
STATUS_UNATTRIBUTED = "unattributed"
STATUSES = (STATUS_ATTRIBUTED, STATUS_OUT_OF_WINDOW, STATUS_UNATTRIBUTED)
MODEL_LAST_CLICK = "last_click"

# Измерение отчёта → колонка conversion_attributions. loss_reason — измерение
# воронки (bid→loss), у конверсии его нет: намеренно отсутствует.
DIM_COLUMNS: dict[str, str] = {
    "campaign": "campaign_id",
    "line_item": "line_item_id",
    "creative": "creative_id",
    "supply_partner": "supply_partner_id",
    "domain": "domain",
    "device": "device_type",
    "country": "geo_country",
    "os": "os",
    "browser": "browser",
    "ad_type": "ad_type",
    "tagid": "tagid",
    "publisher": "publisher_id",
    "bundle": "bundle",
}
# Поля бида, копируемые в атрибуцию (порядок = INSERT ниже).
BID_DIM_FIELDS = ("campaign_id", "line_item_id", "creative_id", "supply_partner_id", "domain",
                  "device_type", "geo_country", "os", "browser", "ad_type", "tagid", "publisher_id", "bundle")
# Gauge conversion_attribution_coverage{dimension} — глобально, за это окно.
COVERAGE_GAUGE_WINDOW_DAYS = 7


def classify(bid: dict | None, converted_at: datetime, window_days: int) -> str:
    """Статус по найденному биду (уже отфильтрован по владельцу) и окну."""
    if not bid or not bid.get("created_at"):
        return STATUS_UNATTRIBUTED
    bid_at = bid["created_at"]
    if bid_at.tzinfo is None:
        bid_at = bid_at.replace(tzinfo=UTC)
    if converted_at - bid_at > timedelta(days=window_days):
        return STATUS_OUT_OF_WINDOW
    return STATUS_ATTRIBUTED


async def record(pool, *, conversion_id, owner_user_id, pixel_id, goal_id, status: str, clid: str | None,
                 bid: dict | None, converted_at: datetime, external_conversion_id: str | None,
                 raw_payload: dict | None, is_test: bool) -> None:
    """Записать атрибуцию новой конверсии. Измерения бида копируются только для attributed."""
    if status not in STATUSES:
        raise ValueError(f"unknown attribution status: {status}")
    dims = [(bid or {}).get(f) if status == STATUS_ATTRIBUTED else None for f in BID_DIM_FIELDS]
    await pool.execute(
        """INSERT INTO conversion_attributions (
               conversion_id, owner_user_id, pixel_id, goal_id, attribution_status, attribution_model,
               clid, bid_at, converted_at,
               campaign_id, line_item_id, creative_id, supply_partner_id, domain, device_type, geo_country,
               os, browser, ad_type, tagid, publisher_id, bundle,
               external_conversion_id, raw_payload, is_test)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24::jsonb,$25)
           ON CONFLICT (conversion_id) DO NOTHING""",
        conversion_id, owner_user_id, pixel_id, goal_id, status, MODEL_LAST_CLICK,
        clid, (bid or {}).get("created_at"), converted_at, *dims,
        external_conversion_id, json.dumps(raw_payload) if raw_payload is not None else None, is_test,
    )
    if status != STATUS_ATTRIBUTED and not is_test:
        obs_metrics.inc_conversion_unattributed(status)


async def owner_breakdown(pool, owner_ids: list[str] | None, d_from: date, d_to: date, dims: list[str]) -> dict:
    """Счётчики по статусам + покрытие по запрошенным измерениям за период.

    coverage[dim] = attributed с ненулевым значением измерения / все конверсии
    владельцев (включая out_of_window/unattributed). Тестовые не считаются.
    owner_ids None = платформенный scope (все владельцы), см. rbac.Scope.owner_ids_param.
    """
    cols = [DIM_COLUMNS[d] for d in dims if d in DIM_COLUMNS]
    covered_sql = "".join(
        f", count(*) FILTER (WHERE attribution_status = 'attributed' AND {c} IS NOT NULL) AS cov_{c}" for c in cols
    )
    row = await pool.fetchrow(
        f"""SELECT count(*) AS total,
                   count(*) FILTER (WHERE attribution_status = 'attributed')    AS attributed,
                   count(*) FILTER (WHERE attribution_status = 'out_of_window') AS out_of_window,
                   count(*) FILTER (WHERE attribution_status = 'unattributed')  AS unattributed
                   {covered_sql}
            FROM conversion_attributions
            WHERE ($1::uuid[] IS NULL OR owner_user_id = ANY($1::uuid[])) AND NOT is_test
              AND converted_at >= $2::date AND converted_at < ($3::date + interval '1 day')""",
        owner_ids, d_from, d_to,
    )
    total = int(row["total"])
    coverage = {d: coverage_ratio(row[f"cov_{DIM_COLUMNS[d]}"], total) for d in dims if d in DIM_COLUMNS}
    return {
        "attributed": int(row["attributed"]),
        "out_of_window": int(row["out_of_window"]),
        "unattributed": int(row["unattributed"]),
        "coverage": coverage,
    }


async def kpi_trustworthy_for_owner(pool, owner_id: str, window_hours: int) -> bool:
    """Можно ли rules/optimizer действовать по CPA/CVR/conversions владельца за окно."""
    from stats_math import conversion_kpi_trustworthy
    row = await pool.fetchrow(
        """SELECT count(*) AS total, count(*) FILTER (WHERE attribution_status = 'attributed') AS attributed
           FROM conversion_attributions
           WHERE owner_user_id = $1 AND NOT is_test AND converted_at >= now() - ($2 || ' hours')::interval""",
        owner_id, str(int(window_hours)),
    )
    return conversion_kpi_trustworthy(row["attributed"], row["total"])


async def refresh_coverage_gauges(pool) -> dict[str, float | None]:
    """conversion_attribution_coverage{dimension} по всем владельцам за COVERAGE_GAUGE_WINDOW_DAYS."""
    dims = list(obs_metrics.COVERAGE_DIMENSIONS)
    cols = [DIM_COLUMNS[d] for d in dims]
    covered_sql = "".join(
        f", count(*) FILTER (WHERE attribution_status = 'attributed' AND {c} IS NOT NULL) AS cov_{c}" for c in cols
    )
    row = await pool.fetchrow(
        f"""SELECT count(*) AS total {covered_sql}
            FROM conversion_attributions
            WHERE NOT is_test AND converted_at >= now() - make_interval(days => $1)""",
        COVERAGE_GAUGE_WINDOW_DAYS,
    )
    out: dict[str, float | None] = {}
    for d in dims:
        ratio = coverage_ratio(row[f"cov_{DIM_COLUMNS[d]}"], row["total"])
        out[d] = ratio
        obs_metrics.set_coverage(d, ratio)
    return out
