"""Автопроверка подключения SSP (Supply Onboarding Check).

Прогоняет интеграционный чек-лист, который SSP выполняет вручную при
подключении DSP, — но автоматически и изнутри:

  1. endpoint отвечает на валидный BidRequest каждого разрешённого формата;
  2. BidResponse соответствует OpenRTB (id, seatbid, price, adm, nurl/burl);
  3. ставка не ниже bid floor партнёра;
  4. неверный auth-токен отклоняется (403), если токен настроен;
  5. неизвестный endpoint_key отклоняется (404);
  6. невалидный JSON отклоняется без падения;
  7. время ответа укладывается в tmax партнёра.

Чистые функции (validate_bid_response, ip_allowed) не трогают сеть и БД —
они покрыты юнит-тестами. run_connection_check оркестрирует прогон.
"""

from __future__ import annotations

import ipaddress
import os
import time
from typing import Any

# --- Адаптер к боевому Go-биддеру ---------------------------------------------
#
# Проверка должна идти через тот же бинарь, что торгует в продакшене. Python
# process_bid_request — legacy-паритет, и результат проверки через него ничего
# не говорит о Go (см. docs/audit/preprod_checklist_2026-09.md D2).
#
# Контракт адаптера = контракту process_bid_request, чтобы run_connection_check
# и её тесты не менялись:
#   dict  -> BidResponse;  None -> no-bid (204);  HTTPException(status) -> отказ.

BIDDER_URL = os.environ.get("BIDDER_URL", "http://bidder-go:8090").rstrip("/")
INTERNAL_CHECK_HEADER = "X-CentralDSP-Internal-Check"  # = bidder-go rtb.internalCheckHeader
TOKEN_HEADER = "X-CentralDSP-Token"  # = bidder-go rtb.TokenHeader


def go_bidder_caller(client=None):
    """Возвращает async-функцию с сигнатурой process_bid_request, бьющую в Go.

    client — httpx.AsyncClient (инжектится в тестах). Внутренний токен
    (RTB_API_TOKEN) в заголовке INTERNAL_CHECK_HEADER даёт Go обойти
    IP-whitelist партнёра: запрос идёт с IP backend'а, а не SSP.
    """
    import httpx
    from fastapi import HTTPException

    internal_token = os.environ.get("RTB_API_TOKEN", "")

    async def call(endpoint_key: str, req: dict, *, verify_token: bool = True,
                   ssp_token: str | None = None, client_ip: str | None = None):
        headers = {"Content-Type": "application/json"}
        if internal_token:
            headers[INTERNAL_CHECK_HEADER] = internal_token
        if ssp_token is not None:
            headers[TOKEN_HEADER] = ssp_token
        elif not verify_token and internal_token:
            # Открытого токена в БД нет (032, только хэш). Проверка формата не
            # должна падать на 403 — просим Go пропустить сверку токена для
            # этого internal-запроса. Негативный тест auth идёт с ssp_token.
            headers["X-CentralDSP-Skip-Token"] = "1"
        url = f"{BIDDER_URL}/rtb/bid/{endpoint_key}"
        own = client is None
        c = client or httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0))
        try:
            try:
                resp = await c.post(url, json=req, headers=headers)
            except httpx.HTTPError as e:
                raise HTTPException(status_code=503, detail=f"Go-биддер недоступен ({BIDDER_URL}): {type(e).__name__}")
        finally:
            if own:
                await c.aclose()
        if resp.status_code == 204:
            return None
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                raise HTTPException(status_code=502, detail="Go-биддер вернул не-JSON на 200")
        raise HTTPException(status_code=resp.status_code, detail=resp.text.strip()[:200])

    return call


async def request_feed_reload(client=None) -> bool:
    """Просит Go немедленно перечитать фид (draft->testing должен попасть в
    снапшот до проверки, а не через 30 с). Ошибка — не фатальна."""
    import httpx

    token = os.environ.get("RTB_API_TOKEN", "")
    if not token:
        return False
    own = client is None
    c = client or httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=1.0))
    try:
        try:
            r = await c.post(f"{BIDDER_URL}/internal/feed/reload", headers={INTERNAL_CHECK_HEADER: token})
            return r.status_code == 200
        except httpx.HTTPError:
            return False
    finally:
        if own:
            await c.aclose()


# --- Чистые функции ---------------------------------------------------------


def ip_allowed(client_ip: str | None, whitelist: list[str]) -> bool:
    """Проверка IP клиента против whitelist (IP или CIDR).

    Пустой whitelist = разрешено всё. Неразборчивые записи игнорируются
    (fail-open по записи, но не по списку: если список непуст и ни одна
    запись не совпала — запрещено).
    """
    if not whitelist:
        return True
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in whitelist:
        entry = str(entry).strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def validate_bid_response(resp: dict | None, *, bid_floor: float = 0.0) -> list[str]:
    """Валидация BidResponse по OpenRTB 2.5. Возвращает список ошибок (пусто = ок).

    Проверяются поля, которые SSP реально валидируют на своей стороне:
    id, seatbid[].bid[]: id, impid, price (>= floor), adm или adm-native, crid.
    """
    errors: list[str] = []
    if resp is None:
        return ["Пустой ответ (no-bid) — нет кандидатов для проверки"]
    if not isinstance(resp, dict):
        return ["BidResponse не является JSON-объектом"]
    if not resp.get("id"):
        errors.append("Отсутствует BidResponse.id (должен совпадать с BidRequest.id)")
    seatbids = resp.get("seatbid")
    if not isinstance(seatbids, list) or not seatbids:
        errors.append("Отсутствует seatbid[]")
        return errors
    for si, sb in enumerate(seatbids):
        bids = sb.get("bid")
        if not isinstance(bids, list) or not bids:
            errors.append(f"seatbid[{si}] без bid[]")
            continue
        for bi, b in enumerate(bids):
            where = f"seatbid[{si}].bid[{bi}]"
            if not b.get("id"):
                errors.append(f"{where}: нет id")
            if not b.get("impid"):
                errors.append(f"{where}: нет impid (ссылка на imp из запроса)")
            price = b.get("price")
            if not isinstance(price, (int, float)) or price <= 0:
                errors.append(f"{where}: price отсутствует или <= 0")
            elif bid_floor and price < bid_floor:
                errors.append(f"{where}: price {price} ниже bid floor {bid_floor}")
            if not b.get("adm"):
                errors.append(f"{where}: нет adm (креатив/markup)")
            if not b.get("crid"):
                errors.append(f"{where}: нет crid (creative id)")
            if not b.get("nurl") and not b.get("burl"):
                errors.append(f"{where}: нет ни nurl, ни burl (win/billing notice)")
    return errors


def make_test_bid_request(ad_type: str, *, request_id: str, bid_floor: float = 0.0) -> dict:
    """Синтетический OpenRTB 2.5 BidRequest под формат (banner/native/video/push)."""
    imp: dict[str, Any] = {"id": "1", "bidfloor": bid_floor, "bidfloorcur": "RUB"}
    if ad_type == "banner":
        imp["banner"] = {"w": 300, "h": 250, "pos": 1}
    elif ad_type == "video":
        imp["video"] = {"mimes": ["video/mp4"], "minduration": 5, "maxduration": 30, "w": 640, "h": 360}
    elif ad_type == "native" or ad_type == "push":
        imp["native"] = {
            "request": '{"ver":"1.2","assets":[{"id":1,"required":1,"title":{"len":90}},{"id":2,"img":{"type":3,"w":1200,"h":627}}]}',
            "ver": "1.2",
        }
    return {
        "id": request_id,
        "at": 2,
        "tmax": 300,
        "cur": ["RUB"],
        "imp": [imp],
        "site": {"id": "check-site", "domain": "connection-check.centraldsp.internal", "page": "https://connection-check.centraldsp.internal/"},
        "device": {
            "ua": "Mozilla/5.0 (Linux; Android 14) CentralDSP-ConnectionCheck/1.0",
            "ip": "203.0.113.10",
            "devicetype": 1,
            "os": "Android",
            "geo": {"country": "RUS"},
        },
        "user": {"id": "check-user-0000"},
        "test": 1,
    }


# --- Оркестрация прогона ------------------------------------------------------


async def run_connection_check(partner: dict, process_bid_request) -> dict:
    """Прогоняет чек-лист подключения для партнёра. Возвращает
    {ran_at, passed, checks: [{key, label, ok, detail}]}.

    process_bid_request передаётся снаружи (из routers.rtb), чтобы модуль
    не импортировал горячий путь и оставался тестируемым.
    """
    from fastapi import HTTPException

    checks: list[dict] = []
    endpoint_key = partner["endpoint_key"]
    bid_floor = float(partner.get("bid_floor") or 0)
    allowed = partner.get("allowed_ad_types") or ["banner"]
    tmax_ms = int(partner.get("tmax_ms") or 200)
    # Открытый токен в БД не хранится (миграция 032) — есть только хэш.
    # Формат-проверки идут с verify_token=False, негативный тест ниже
    # проверяет, что неверный токен действительно отклоняется.
    token_configured = bool(partner.get("auth_token_hash"))

    def add(key: str, label: str, ok: bool, detail: str = "") -> None:
        checks.append({"key": key, "label": label, "ok": ok, "detail": detail})

    # 1) Ответ на BidRequest каждого разрешённого формата + валидация схемы + тайминг
    for ad_type in allowed:
        req = make_test_bid_request(ad_type, request_id=f"check-{ad_type}", bid_floor=bid_floor)
        started = time.monotonic()
        try:
            resp = await process_bid_request(endpoint_key, req, verify_token=False)
            elapsed_ms = (time.monotonic() - started) * 1000
        except HTTPException as e:
            add(f"bid_{ad_type}", f"BidRequest ({ad_type})", False, f"HTTP {e.status_code}: {e.detail}")
            continue
        except Exception as e:  # noqa: BLE001
            add(f"bid_{ad_type}", f"BidRequest ({ad_type})", False, f"Ошибка биддера: {type(e).__name__}")
            continue
        if resp is None:
            add(
                f"bid_{ad_type}", f"BidRequest ({ad_type})", False,
                "No-bid: нет активной кампании с этим форматом (создайте кампанию с креативом нужного формата и бюджетом)",
            )
            continue
        schema_errors = validate_bid_response(resp, bid_floor=bid_floor)
        if schema_errors:
            add(f"bid_{ad_type}", f"BidRequest ({ad_type})", False, "; ".join(schema_errors[:3]))
        else:
            add(f"bid_{ad_type}", f"BidRequest ({ad_type})", True, f"BidResponse валиден, ответ за {elapsed_ms:.0f} мс")
        add(
            f"tmax_{ad_type}", f"Тайминг ({ad_type}) ≤ {tmax_ms} мс",
            elapsed_ms <= tmax_ms,
            f"{elapsed_ms:.0f} мс" + ("" if elapsed_ms <= tmax_ms else f" — превышает tmax {tmax_ms} мс"),
        )

    # 2) Auth: неверный токен должен отклоняться (только если токен настроен)
    if token_configured:
        req = make_test_bid_request("banner", request_id="check-auth", bid_floor=bid_floor)
        try:
            await process_bid_request(endpoint_key, req, ssp_token="invalid-token-check")
            add("auth", "Отклонение неверного токена", False, "Запрос с неверным токеном НЕ был отклонён")
        except HTTPException as e:
            add("auth", "Отклонение неверного токена", e.status_code == 403, f"HTTP {e.status_code}")
        except Exception:  # noqa: BLE001
            add("auth", "Отклонение неверного токена", False, "Неожиданная ошибка вместо HTTP 403")
    else:
        add("auth", "Auth-токен", True, "Токен не настроен — аутентификация только по секретному URL (рекомендуем включить токен)")

    # 3) Неизвестный endpoint_key -> 404
    try:
        await process_bid_request("nonexistent-endpoint-check", make_test_bid_request("banner", request_id="check-404"))
        add("unknown_endpoint", "Отклонение неизвестного endpoint", False, "Запрос на несуществующий ключ НЕ был отклонён")
    except HTTPException as e:
        add("unknown_endpoint", "Отклонение неизвестного endpoint", e.status_code == 404, f"HTTP {e.status_code}")
    except Exception:  # noqa: BLE001
        add("unknown_endpoint", "Отклонение неизвестного endpoint", False, "Неожиданная ошибка вместо HTTP 404")

    passed = all(c["ok"] for c in checks)
    return {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "passed": passed,
        "checks": checks,
    }
