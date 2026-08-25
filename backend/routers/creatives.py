import json

from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from rbac import require_campaign_editor
from db import get_pool
from schemas import CreativeIn, CreativePatch

router = APIRouter(prefix="/creatives", tags=["creatives"])

_REQUIRED_BY_TYPE = {
    "banner": ["width", "height", "click_url"],
    "native": ["native_title", "click_url"],
    "video": ["click_url"],
    "push": ["native_title", "asset_url", "click_url"],
}


def _serialize(row) -> dict:
    d = dict(row)
    for k in ("id", "campaign_id", "user_id", "line_item_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    return d


def _validate_type_fields(body: CreativeIn) -> None:
    missing = [f for f in _REQUIRED_BY_TYPE[body.type] if not getattr(body, f)]
    if body.type == "banner" and not (body.asset_url or body.html_snippet):
        missing.append("asset_url или html_snippet")
    if body.type == "banner" and body.subtype == "html5" and not body.html_snippet:
        missing.append("html_snippet (для HTML5-баннера)")
    if body.type == "video" and not (body.video_url or body.vast_xml):
        missing.append("video_url или vast_xml")
    if body.type == "video" and not body.video_duration and not body.vast_xml:
        missing.append("video_duration")
    if missing:
        raise HTTPException(status_code=422, detail=f"Для типа {body.type} обязательны поля: {', '.join(missing)}")


@router.get("")
async def list_creatives(campaign_id: str | None = None, user: dict = Depends(current_user)):
    pool = await get_pool()
    if campaign_id:
        rows = await pool.fetch(
            "SELECT * FROM creatives WHERE user_id = $1 AND campaign_id = $2 ORDER BY created_at DESC",
            user["id"], campaign_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM creatives WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
        )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_creative(body: CreativeIn, user: dict = Depends(require_campaign_editor)):
    _validate_type_fields(body)
    pool = await get_pool()
    camp = await pool.fetchval(
        "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", body.campaign_id, user["id"]
    )
    if not camp:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    row = await pool.fetchrow(
        """
        INSERT INTO creatives (campaign_id, line_item_id, user_id, name, type, subtype, width, height,
                               asset_url, click_url, html_snippet, native_title, native_description,
                               native_cta, native_icon_url, video_url, video_duration, vast_xml, crop,
                               imp_tracker_url, click_tracker_url, status)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,'approved') RETURNING *
        """,
        body.campaign_id, body.line_item_id, user["id"], body.name, body.type, body.subtype, body.width,
        body.height, body.asset_url, body.click_url, body.html_snippet, body.native_title,
        body.native_description, body.native_cta, body.native_icon_url, body.video_url,
        body.video_duration, body.vast_xml, json.dumps(body.crop) if body.crop else None,
        body.imp_tracker_url, body.click_tracker_url,
    )
    return _serialize(row)


@router.patch("/{creative_id}")
async def update_creative(creative_id: str, body: CreativePatch, user: dict = Depends(require_campaign_editor)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    pool = await get_pool()
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        sets.append(f"{k} = ${i}")
        values.append(v)
    row = await pool.fetchrow(
        f"UPDATE creatives SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        creative_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Креатив не найден")
    return _serialize(row)


@router.delete("/{creative_id}", status_code=204)
async def delete_creative(creative_id: str, user: dict = Depends(require_campaign_editor)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM creatives WHERE id = $1 AND user_id = $2", creative_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Креатив не найден")
