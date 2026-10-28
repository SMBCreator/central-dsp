"""Справочник «домен → тематика IAB» (Фаза 3 ТЗ контекстного таргетинга).

Чистая часть (нормализация, валидация, разбор CSV, слияние обученных кодов)
не зависит от БД и покрыта тестами без инфраструктуры. Часть с БД/Redis —
CRUD для кабинета, фид для биддера и перенос обученных записей из Redis.

Обучение: биддер пишет в хэш Redis `domcat:learn` поля двух видов
  `<domain>`          — число наблюдений домена (идёт в `hits`);
  `<domain>|<IABx-y>` — сколько раз видели категорию у домена.
`/cron/domcat-learn` забирает хэш целиком (RENAME → HSCAN), сливает в таблицу
с `source='learned'`. Ручные записи не трогаются.
"""
from __future__ import annotations

import os
import re
from collections import Counter

LEARN_KEY = "domcat:learn"
LEARN_PROC_KEY = "domcat:learn:proc"

MAX_CODES_PER_DOMAIN = int(os.environ.get("DOMCAT_MAX_CODES", "5"))
MAX_IMPORT_ROWS = 5000
MAX_MANUAL_CODES = 10
FEED_MIN_HITS = int(os.environ.get("DOMCAT_FEED_MIN_HITS", "3"))
FEED_MAX_ROWS = int(os.environ.get("DOMCAT_FEED_MAX_ROWS", "200000"))
# Обученные записи, которые так и не набрали порог, не хранятся вечно.
LEARNED_STALE_DAYS = 90

_IAB_RE = re.compile(r"^IAB\d{1,3}(?:-\d{1,3})?$")
_LABEL_RE = re.compile(r"^(?:xn--)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_domain(raw: str | None) -> str | None:
    """Нижний регистр, без схемы/пути/порта, `www.` и точки на конце.
    None — если это не похоже на имя домена."""
    if not isinstance(raw, str):
        return None
    d = raw.strip().lower()
    if not d or len(d) > 253:
        return None
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in d or " " in d:
        return None
    if ":" in d:
        host, _, port = d.rpartition(":")
        if not port.isdigit():
            return None
        d = host
    d = d.rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    if not d or len(d) > 253:
        return None
    try:
        d = d.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = d.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.match(x) for x in labels):
        return None
    if labels[-1].isdigit():  # IP-адрес, а не домен
        return None
    return d


def normalize_iab_code(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    c = raw.strip().upper()
    return c if _IAB_RE.match(c) else None


def normalize_codes(raw: list[str] | None, *, limit: int = MAX_MANUAL_CODES) -> tuple[list[str], list[str]]:
    """-> (коды без дублей в порядке ввода, недопустимые значения)."""
    out: list[str] = []
    bad: list[str] = []
    for item in raw or []:
        code = normalize_iab_code(item)
        if code is None:
            bad.append(str(item))
        elif code not in out:
            out.append(code)
    if len(out) > limit:
        bad.append(f"слишком много кодов (максимум {limit})")
    return out, bad


def parse_import_csv(text: str) -> tuple[list[dict], list[dict]]:
    """CSV `domain;IAB1,IAB2` -> (валидные строки, ошибки с номерами строк).

    Разделитель полей — `;` (запятая внутри поля — между кодами). Первая строка
    с заголовком `domain` пропускается. Дубликаты доменов — ошибка второй и
    следующих строк. Превышение лимита строк — одна ошибка и пустой результат:
    частичный импорт неочевиден для оператора.
    """
    rows: list[dict] = []
    errors: list[dict] = []
    seen: dict[str, int] = {}
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    data = [(i + 1, ln) for i, ln in enumerate(lines) if ln.strip()]
    if data and data[0][1].split(";", 1)[0].strip().lower() in ("domain", "домен"):
        data = data[1:]
    if len(data) > MAX_IMPORT_ROWS:
        return [], [{"line": 0, "domain": "", "error": f"слишком много строк: {len(data)} (максимум {MAX_IMPORT_ROWS})"}]
    for lineno, ln in data:
        domain_raw, _, codes_raw = ln.partition(";")
        domain = normalize_domain(domain_raw)
        if domain is None:
            errors.append({"line": lineno, "domain": domain_raw.strip()[:80], "error": "некорректное имя домена"})
            continue
        codes, bad = normalize_codes([c for c in re.split(r"[,\s]+", codes_raw) if c])
        if not codes and not bad:
            errors.append({"line": lineno, "domain": domain, "error": "не указаны коды IAB"})
            continue
        if bad:
            errors.append({"line": lineno, "domain": domain, "error": "недопустимые коды: " + ", ".join(bad)})
            continue
        if domain in seen:
            errors.append({"line": lineno, "domain": domain, "error": f"дубликат домена (первая строка: {seen[domain]})"})
            continue
        seen[domain] = lineno
        rows.append({"domain": domain, "iab_codes": codes})
    return rows, errors


def merge_learned_codes(existing: list[str], batch_counts: Counter, *, limit: int = MAX_CODES_PER_DOMAIN) -> list[str]:
    """Топ-`limit` кодов: сначала по частоте в свежей пачке, затем ранее известные.
    Ранее известные не вытесняются случайным шумом, пока в пачке есть место."""
    ranked = [c for c, _ in sorted(batch_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    for c in existing:
        if c not in ranked:
            ranked.append(c)
    return ranked[:limit]


def parse_learn_entries(items: dict[str, str]) -> dict[str, dict]:
    """Поля хэша `domcat:learn` -> {domain: {"hits": int, "cats": Counter}}.
    Мусорные поля (не домен, не IAB-код, не число) молча пропускаются."""
    out: dict[str, dict] = {}
    for field, value in items.items():
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        dom_raw, sep, code_raw = field.partition("|")
        domain = normalize_domain(dom_raw)
        if domain is None:
            continue
        entry = out.setdefault(domain, {"hits": 0, "cats": Counter()})
        if not sep:
            entry["hits"] += n
            continue
        code = normalize_iab_code(code_raw)
        if code:
            entry["cats"][code] += n
    return {d: e for d, e in out.items() if e["cats"]}


# ---------------------------------------------------------------- БД

async def list_domains(pool, *, q: str = "", source: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    where, args = [], []
    if q:
        args.append("%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        where.append(f"domain LIKE ${len(args)}")
    if source in ("manual", "learned"):
        args.append(source)
        where.append(f"source = ${len(args)}")
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    total = await pool.fetchval(f"SELECT count(*) FROM domain_categories{cond}", *args)
    args += [limit, offset]
    rows = await pool.fetch(
        f"SELECT domain, iab_codes, source, hits, updated_at FROM domain_categories{cond} "
        f"ORDER BY source = 'manual' DESC, hits DESC, domain LIMIT ${len(args) - 1} OFFSET ${len(args)}",
        *args,
    )
    return {"total": total, "items": [_row(r) for r in rows]}


def _row(r) -> dict:
    return {
        "domain": r["domain"],
        "iab_codes": list(r["iab_codes"] or []),
        "source": r["source"],
        "hits": r["hits"],
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


async def upsert_manual(pool, domain: str, codes: list[str]) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO domain_categories (domain, iab_codes, source, updated_at)
           VALUES ($1, $2, 'manual', now())
           ON CONFLICT (domain) DO UPDATE
              SET iab_codes = EXCLUDED.iab_codes, source = 'manual', updated_at = now()
           RETURNING domain, iab_codes, source, hits, updated_at""",
        domain, codes,
    )
    return _row(row)


async def delete_domain(pool, domain: str) -> bool:
    return (await pool.execute("DELETE FROM domain_categories WHERE domain = $1", domain)).endswith(" 1")


async def import_rows(pool, rows: list[dict]) -> dict:
    """Применить уже проверенные строки одной транзакцией. -> счётчики."""
    created = updated = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                inserted = await conn.fetchval(
                    """INSERT INTO domain_categories (domain, iab_codes, source, updated_at)
                       VALUES ($1, $2, 'manual', now())
                       ON CONFLICT (domain) DO UPDATE
                          SET iab_codes = EXCLUDED.iab_codes, source = 'manual', updated_at = now()
                       RETURNING (xmax = 0)""",
                    r["domain"], r["iab_codes"],
                )
                created += 1 if inserted else 0
                updated += 0 if inserted else 1
    return {"created": created, "updated": updated}


async def feed_payload(pool) -> dict:
    """Карта для биддера: manual целиком + learned с hits >= порога, с лимитом."""
    rows = await pool.fetch(
        """SELECT domain, iab_codes FROM domain_categories
            WHERE cardinality(iab_codes) > 0 AND (source = 'manual' OR hits >= $1)
            ORDER BY source = 'manual' DESC, hits DESC, domain
            LIMIT $2""",
        FEED_MIN_HITS, FEED_MAX_ROWS + 1,
    )
    truncated = len(rows) > FEED_MAX_ROWS
    rows = rows[:FEED_MAX_ROWS]
    return {
        "count": len(rows),
        "truncated": truncated,
        "domains": {r["domain"]: list(r["iab_codes"]) for r in rows},
    }


async def flush_learned(pool, redis) -> dict:
    """Перенести обученные записи из Redis в таблицу. Идемпотентно по повтору:
    хэш сначала переименовывается, поэтому параллельные записи биддера не
    теряются, а упавший посередине запуск доберёт остаток в следующий раз."""
    if not await redis.exists(LEARN_PROC_KEY):
        if not await redis.exists(LEARN_KEY):
            return {"domains": 0, "skipped": "empty"}
        try:
            await redis.rename(LEARN_KEY, LEARN_PROC_KEY)
        except Exception:  # ключ могли забрать параллельным запуском
            return {"domains": 0, "skipped": "empty"}

    items: dict[str, str] = {}
    cursor = 0
    while True:
        cursor, batch = await redis.hscan(LEARN_PROC_KEY, cursor, count=1000)
        items.update(batch)
        if cursor == 0 or cursor == "0":
            break

    entries = parse_learn_entries(items)
    merged = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for domain, e in entries.items():
                prev = await conn.fetchrow(
                    "SELECT iab_codes, source FROM domain_categories WHERE domain = $1 FOR UPDATE", domain
                )
                if prev and prev["source"] == "manual":
                    continue
                codes = merge_learned_codes(list(prev["iab_codes"]) if prev else [], e["cats"])
                await conn.execute(
                    """INSERT INTO domain_categories (domain, iab_codes, source, hits, updated_at)
                       VALUES ($1, $2, 'learned', $3, now())
                       ON CONFLICT (domain) DO UPDATE
                          SET iab_codes = EXCLUDED.iab_codes,
                              hits = domain_categories.hits + EXCLUDED.hits,
                              updated_at = now()
                        WHERE domain_categories.source = 'learned'""",
                    domain, codes, max(e["hits"], 1),
                )
                merged += 1
            purged = await conn.execute(
                "DELETE FROM domain_categories WHERE source = 'learned' AND hits < $1 "
                "AND updated_at < now() - make_interval(days => $2)",
                FEED_MIN_HITS, LEARNED_STALE_DAYS,
            )
    await redis.delete(LEARN_PROC_KEY)
    return {"domains": merged, "purged": int(purged.split()[-1]) if purged else 0}
