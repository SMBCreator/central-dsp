"""Публичные ручки ID sync (план predeploy A.3; docs/ID_SYNC.md).

GET  /sync/{partner_key}/in   — SSP -> DSP: SSP зовёт наш пиксель со своим ID.
GET  /sync/{partner_key}/out  — DSP -> SSP: подписанная ссылка, редирект на sync URL SSP.
GET  /sync/optout             — отказ: соответствия удалены, cookie = optout.
POST /sync/delete             — удаление соответствий текущего dsp_uid.

partner_key — UUID SSP-партнёра (публичен, в отличие от endpoint_key/токена).
Ручки не читают и не ставят cookie кабинета: работают только с dsp_uid.
Ответ не раскрывает, найден ли ID: всегда gif 200 / 302 / 200 text.
При ID_SYNC_ENABLED=off — 404, как будто маршрутов нет.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

import id_sync
import obs_metrics
from db import get_pool

log = logging.getLogger("id_sync")

router = APIRouter(prefix="/sync", tags=["id-sync"])

_GIF = bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b")
_NO_STORE = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _require_enabled() -> None:
    if not id_sync.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _gif() -> Response:
    return Response(content=_GIF, media_type="image/gif", headers=dict(_NO_STORE))


def _set_uid_cookie(resp: Response, value: str) -> None:
    resp.set_cookie(
        id_sync.COOKIE_NAME, value, max_age=id_sync.cookie_max_age(),
        secure=True, httponly=True, samesite="none", path="/sync",
    )


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=302, headers=dict(_NO_STORE))


def _client_ip(request: Request) -> str:
    # X-Forwarded-* от Caddy уже применены uvicorn (TRUSTED_PROXIES); сырой
    # XFF клиента сюда не попадает.
    return request.client.host if request.client else "unknown"


@router.get("/{partner_key}/in")
async def sync_in(partner_key: str, request: Request):
    _require_enabled()
    pool = await get_pool()
    partner = await id_sync.load_partner(pool, partner_key)
    if partner is None:
        obs_metrics.inc_id_sync_request(None, "unknown_partner")
        return _gif()
    if await id_sync.rate_limited(_client_ip(request), partner.id):
        obs_metrics.inc_id_sync_request(partner.id, "rate_limited")
        return _gif()

    cookie = request.cookies.get(id_sync.COOKIE_NAME)
    if cookie == id_sync.OPTOUT_VALUE:
        obs_metrics.inc_id_sync_request(partner.id, "optout")
        return _gif()

    params = request.query_params
    ssp_uid = params.get(partner.identity.sync_in_param)
    if not id_sync.valid_ssp_uid(ssp_uid):
        obs_metrics.inc_id_sync_request(partner.id, "invalid")
        return _gif()

    scope = id_sync.consent_scope(
        partner.identity, params.get("gdpr"), params.get("gdpr_consent"), params.get("us_privacy")
    )
    if scope is None:
        obs_metrics.inc_id_sync_request(partner.id, "consent_denied")
        obs_metrics.inc("id_sync_consent_denied_total")
        return _gif()

    uid_hash = id_sync.hash_ssp_uid(partner.salt, ssp_uid)
    dsp_uid = cookie if id_sync.valid_dsp_uid(cookie) else None
    if dsp_uid is None:
        # Cookie нет (новый браузер / сброс) — переиспользуем прежний dsp_uid
        # этого ssp_uid, чтобы аудитории и частота не обнулились.
        dsp_uid = await id_sync.existing_dsp_uid(pool, partner, uid_hash) or id_sync.new_dsp_uid()
    created = await id_sync.store_mapping(pool, partner, uid_hash, dsp_uid, scope)
    if created:
        obs_metrics.inc_id_sync_created(partner.id)
    obs_metrics.inc_id_sync_request(partner.id, "mapped" if created else "refreshed")
    log.info("id_sync in", extra={"partner_id": partner.id, "ssp_uid_h": id_sync.hash_for_log(ssp_uid), "created": created})

    resp: Response = _gif()
    if partner.identity.sync_callback_url_template:
        url = id_sync.build_redirect(partner.identity, dsp_uid)
        if url:
            resp = _redirect(url)
        else:
            obs_metrics.inc("id_sync_redirect_rejected_total")
    _set_uid_cookie(resp, dsp_uid)
    return resp


@router.get("/{partner_key}/out")
async def sync_out(partner_key: str, request: Request, ts: str | None = None, sig: str | None = None):
    _require_enabled()
    pool = await get_pool()
    partner = await id_sync.load_partner(pool, partner_key)
    if partner is None:
        obs_metrics.inc_id_sync_request(None, "unknown_partner")
        return _gif()
    if await id_sync.rate_limited(_client_ip(request), partner.id):
        obs_metrics.inc_id_sync_request(partner.id, "rate_limited")
        return _gif()
    cookie = request.cookies.get(id_sync.COOKIE_NAME)
    if cookie == id_sync.OPTOUT_VALUE:
        obs_metrics.inc_id_sync_request(partner.id, "optout")
        return _gif()
    if not id_sync.verify_signature(partner_key, ts, sig) or not await id_sync.claim_signature(sig or ""):
        obs_metrics.inc_id_sync_request(partner.id, "out_rejected")
        obs_metrics.inc("id_sync_redirect_rejected_total")
        return _gif()
    dsp_uid = cookie if id_sync.valid_dsp_uid(cookie) else id_sync.new_dsp_uid()
    url = id_sync.build_redirect(partner.identity, dsp_uid)
    if not url:
        obs_metrics.inc_id_sync_request(partner.id, "out_rejected")
        obs_metrics.inc("id_sync_redirect_rejected_total")
        return _gif()
    obs_metrics.inc_id_sync_request(partner.id, "out_redirect")
    resp = _redirect(url)
    _set_uid_cookie(resp, dsp_uid)
    return resp


async def _forget(request: Request) -> int:
    cookie = request.cookies.get(id_sync.COOKIE_NAME)
    if not id_sync.valid_dsp_uid(cookie):
        return 0
    return await id_sync.delete_by_dsp_uid(await get_pool(), cookie)


_OPTOUT_TEXT = "Вы отказались от сопоставления идентификаторов. Данные удалены.\n"


@router.get("/optout")
async def sync_optout(request: Request):
    _require_enabled()
    await _forget(request)
    obs_metrics.inc_id_sync_request(None, "optout")
    resp = Response(content=_OPTOUT_TEXT, media_type="text/plain; charset=utf-8", headers=dict(_NO_STORE))
    _set_uid_cookie(resp, id_sync.OPTOUT_VALUE)
    return resp


@router.post("/delete")
async def sync_delete(request: Request):
    _require_enabled()
    await _forget(request)
    resp = Response(status_code=204, headers=dict(_NO_STORE))
    resp.delete_cookie(id_sync.COOKIE_NAME, path="/sync", secure=True, httponly=True, samesite="none")
    return resp
