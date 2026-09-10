import json
import os
import re
import struct
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from auth import current_user
from rbac import assert_in_scope, assert_owned_by, assert_refs_owned, is_admin, require_creative_manager, scope_for
from notify import notify_admins
from db import get_pool
from ord.service import campaign_targets_ru, register_creative, supersede_current
from schemas import CreativeIn, CreativePatch

router = APIRouter(prefix="/creatives", tags=["creatives"])

# Поля, изменение которых меняет то, что увидит пользователь — требуют повторной модерации
_CONTENT_FIELDS = frozenset({
    "asset_url", "click_url", "html_snippet", "native_title", "native_description",
    "native_cta", "native_icon_url", "video_url", "vast_xml",
})

# Юридически значимые поля маркировки. После выдачи erid (creatives.ord_locked_at)
# их менять нельзя: erid выдан на конкретного заказчика/договор/тематику. Смена =
# новый креатив (новая модерация, новый erid, отдельная статистика) — так же, как
# в ОРД: новые данные договора/заказчика = новый externalCreativeId (B13, L-02).
_LEGAL_FIELDS = frozenset({"counterparty_id", "contract_id", "kktu_code"})

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


async def _mark_self_approved(pool, row) -> dict:
    """Маркировка креатива, который админ одобрил сам себе (создание / правка
    содержимого). Тот же путь, что /moderation/approve: approved без erid для
    РФ-трафика = креатив молча выпадает из фида. Возвращает сериализованный
    креатив с полем `ord` для UI."""
    out = _serialize(row)
    if not await campaign_targets_ru(pool, str(row["campaign_id"])):
        out["ord"] = {"status": "not_required", "error": None}
        return out
    res = await register_creative(pool, dict(row))
    if res["outcome"] == "ok":
        fresh = await pool.fetchrow("SELECT * FROM creatives WHERE id = $1", row["id"])
        out = _serialize(fresh)
    out["ord"] = {"status": res["outcome"], "error": res.get("error")}
    return out


# Ссылочные поля креатива, владелец — владелец кампании/креатива.
_REF_TABLES = {
    "counterparty_id": ("counterparties", "Контрагент не найден"),
    "contract_id": ("ord_contracts", "Договор не найден"),
}


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
    scope = await scope_for(user)
    if campaign_id:
        rows = await pool.fetch(
            f"SELECT * FROM creatives WHERE {scope.where('user_id', 1)} AND campaign_id = $3 ORDER BY created_at DESC",
            *scope.params, campaign_id,
        )
    else:
        rows = await pool.fetch(
            f"SELECT * FROM creatives WHERE {scope.where('user_id', 1)} ORDER BY created_at DESC", *scope.params
        )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_creative(body: CreativeIn, user: dict = Depends(require_creative_manager)):
    _validate_type_fields(body)
    pool = await get_pool()
    camp = await assert_in_scope(
        pool, "campaigns", body.campaign_id, await scope_for(user), "Кампания не найдена", columns="id, user_id"
    )
    # Креатив принадлежит владельцу кампании. Ссылочные id должны принадлежать
    # ему же (и группа — этой кампании): иначе креатив можно повесить на чужую
    # группу (уйдёт в чужой фид) или подписать в ОРД чужим контрагентом/договором.
    owner = str(camp["user_id"])
    if body.line_item_id:
        await assert_owned_by(
            pool, "line_items", body.line_item_id, owner, "Группа объявлений не найдена",
            extra_where="campaign_id = $3", extra_params=(body.campaign_id,),
        )
    await assert_refs_owned(
        pool, owner, {"counterparty_id": body.counterparty_id, "contract_id": body.contract_id}, _REF_TABLES
    )
    # Админы модерируют сами себя: креатив сразу approved. Остальные — на модерацию.
    status = "approved" if is_admin(user) else "pending"
    row = await pool.fetchrow(
        """
        INSERT INTO creatives (campaign_id, line_item_id, user_id, name, type, subtype, width, height,
                               asset_url, click_url, html_snippet, native_title, native_description,
                               native_cta, native_icon_url, video_url, video_duration, vast_xml, crop,
                               imp_tracker_url, click_tracker_url, status, counterparty_id,
                               kktu_code, kktu_label, iab_categories, attrs, contract_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,
                $26::text[], $27::smallint[], $28) RETURNING *
        """,
        body.campaign_id, body.line_item_id, owner, body.name, body.type, body.subtype, body.width,
        body.height, body.asset_url, body.click_url, body.html_snippet, body.native_title,
        body.native_description, body.native_cta, body.native_icon_url, body.video_url,
        body.video_duration, body.vast_xml, json.dumps(body.crop) if body.crop else None,
        body.imp_tracker_url, body.click_tracker_url, status, body.counterparty_id,
        body.kktu_code, body.kktu_label, body.iab_categories, body.attrs, body.contract_id,
    )
    if status == "approved":
        return await _mark_self_approved(pool, row)
    await notify_admins(
        "moderation_pending",
        f"Новый креатив на модерации: «{body.name}»",
        f"Пользователь {user['email']} загрузил креатив в кампанию.",
        "/moderation",
    )
    return _serialize(row)


@router.patch("/{creative_id}")
async def update_creative(creative_id: str, body: CreativePatch, user: dict = Depends(require_creative_manager)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    pool = await get_pool()
    cur = await assert_in_scope(
        pool, "creatives", creative_id, await scope_for(user), "Креатив не найден",
        columns="id, user_id, status, erid, name, ord_locked_at, counterparty_id, contract_id, kktu_code",
    )
    owner = str(cur["user_id"])
    await assert_refs_owned(pool, owner, {k: updates.get(k) for k in _REF_TABLES}, _REF_TABLES)
    if cur["ord_locked_at"] is not None:
        changed_legal = sorted(
            k for k in _LEGAL_FIELDS & set(updates)
            if str(updates[k] or "") != str(cur[k] or "")
        )
        if changed_legal:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ord_locked",
                    "fields": changed_legal,
                    "message": (
                        "Креативу уже выдан erid: контрагент, договор и ККТУ заморожены. "
                        "Для другого заказчика/договора/тематики создайте новый креатив — "
                        "он пройдёт модерацию и получит собственный erid."
                    ),
                },
            )
    # Изменение видимого содержимого одобренного креатива (картинка, ссылка,
    # текст, видео) отправляет его обратно на модерацию — иначе после approve
    # можно подменить баннер. Админ модерирует сам себя: статус не сбрасываем,
    # но erid выдан на конкретное содержимое — снимаем и перерегистрируем.
    remoderate = False
    remark = False
    if _CONTENT_FIELDS & set(updates) and "status" not in updates and cur["status"] == "approved":
        updates["erid"] = None
        if is_admin(user):
            remark = True
        else:
            remoderate = True
            updates["status"] = "pending"
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        sets.append(f"{k} = ${i}")
        values.append(v)
    row = await pool.fetchrow(
        f"UPDATE creatives SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        creative_id, owner, *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Креатив не найден")
    if remoderate or remark:
        # Старая версия остаётся в истории со своим erid: статистика за часы до
        # superseded_at уйдёт в ОРД под ним, новая версия получит новый erid.
        await supersede_current(pool, creative_id)
    if remark:
        return await _mark_self_approved(pool, row)
    if remoderate:
        await notify_admins(
            "moderation_pending",
            f"Креатив изменён, повторная модерация: «{row['name']}»",
            f"Пользователь {user['email']} изменил содержимое одобренного креатива.",
            "/moderation",
        )
    return _serialize(row)


@router.delete("/{creative_id}", status_code=204)
async def delete_creative(creative_id: str, user: dict = Depends(require_creative_manager)):
    pool = await get_pool()
    scope = await scope_for(user)
    result = await pool.execute(
        f"DELETE FROM creatives WHERE id = $1 AND {scope.where('user_id', 2)}", creative_id, *scope.params
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Креатив не найден")


# ---------------------------------------------------------------------------
# File upload for creative assets (images / video)
# ---------------------------------------------------------------------------

_ALLOWED_UPLOAD_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}

# Лимиты по типу файла (байты). Настраиваются через env, чтобы прод на VPS мог
# поднять потолок для видео без правки кода (см. docs/DEPLOYMENT.md, nginx
# client_max_body_size должен быть >= MAX_VIDEO_UPLOAD_BYTES).
_MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_UPLOAD_BYTES", str(2 * 1024 * 1024)))   # 2 МБ
_MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_UPLOAD_BYTES", str(50 * 1024 * 1024)))  # 50 МБ
_CHUNK = 1024 * 1024
_UPLOAD_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp|mp4|webm)$")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} Б"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} КБ"
    return f"{n / 1024 / 1024:.1f} МБ".replace(".", ",")


def sniff_upload(head: bytes) -> str | None:
    """Определяет реальный MIME по сигнатуре файла (первые байты).

    Не доверяем Content-Type из multipart: браузер/клиент может подставить что угодно,
    а отдавать чужой HTML/SVG с нашего origin под видом картинки — XSS-риск.
    Возвращает MIME из _ALLOWED_UPLOAD_TYPES или None.
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    # ISO BMFF (mp4/m4v/mov): размер атома (4 байта) + 'ftyp'
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand[:3] in (b"isc", b"iso", b"mp4", b"M4V", b"avc", b"dash", b"qt ") or brand in (b"mmp4", b"MSNV"):
            return "video/mp4"
        return "video/mp4"
    # Matroska/WebM EBML header
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    return None


def image_dimensions(head: bytes) -> tuple[int, int] | None:
    """Ширина/высота для PNG/GIF/JPEG(SOF)/WebP(VP8/VP8L/VP8X) из первых ~64 КБ без Pillow."""
    try:
        if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
            return struct.unpack(">II", head[16:24])
        if head.startswith((b"GIF87a", b"GIF89a")):
            return struct.unpack("<HH", head[6:10])
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            chunk = head[12:16]
            if chunk == b"VP8X":
                w = 1 + int.from_bytes(head[24:27], "little")
                h = 1 + int.from_bytes(head[27:30], "little")
                return w, h
            if chunk == b"VP8L":
                b = head[21:25]
                w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                return w, h
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", head[26:30])
                return w & 0x3FFF, h & 0x3FFF
        if head.startswith(b"\xff\xd8"):
            i = 2
            n = len(head)
            while i + 9 < n:
                if head[i] != 0xFF:
                    i += 1
                    continue
                marker = head[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg_len = struct.unpack(">H", head[i + 2 : i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", head[i + 5 : i + 9])
                    return w, h
                i += 2 + seg_len
    except (struct.error, IndexError):
        return None
    return None


@router.post("/upload")
async def upload_creative_asset(
    request: Request,
    file: UploadFile,
    user: dict = Depends(require_creative_manager),
):
    """Загрузка файла креатива; возвращает путь для asset_url/video_url + размеры.

    Валидация: MIME по сигнатуре (не по заголовку клиента), лимит по типу,
    стриминговая запись на диск (не держим видео целиком в памяти).
    При превышении лимита файл удаляется, ответ 413 с понятным текстом.
    """
    head = await file.read(64 * 1024)
    if not head:
        raise HTTPException(400, "Пустой файл")
    mime = sniff_upload(head)
    if not mime:
        raise HTTPException(415, "Допустимы PNG, JPEG, GIF, WebP, MP4, WebM. Файл не распознан по содержимому.")
    ext = _ALLOWED_UPLOAD_TYPES[mime]
    is_video = mime.startswith("video/")
    limit = _MAX_VIDEO_BYTES if is_video else _MAX_IMAGE_BYTES
    too_big = HTTPException(
        413,
        f"Файл больше {_fmt_size(limit)} — лимит для {'видео' if is_video else 'изображений'}.",
    )
    if len(head) > limit:
        raise too_big

    from main import UPLOAD_DIR

    name = f"{uuid.uuid4().hex}{ext}"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    dest = os.path.join(UPLOAD_DIR, name)
    size = 0
    try:
        with open(dest, "wb") as fh:
            fh.write(head)
            size = len(head)
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        413,
                        f"Файл больше {_fmt_size(limit)} — лимит для {'видео' if is_video else 'изображений'}.",
                    )
                fh.write(chunk)
    except HTTPException:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise

    dims = None if is_video else image_dimensions(head)
    public_base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    base = public_base or str(request.base_url).rstrip("/")
    return {
        "url": f"{base}/uploads/{name}",
        "path": f"/uploads/{name}",
        "size": size,
        "mime": mime,
        "width": dims[0] if dims else None,
        "height": dims[1] if dims else None,
    }


@router.delete("/upload/{name}", status_code=204)
async def delete_upload(name: str, user: dict = Depends(require_creative_manager)):
    """Удаление «висящего» файла, если создание креатива не состоялось.

    Имя строго по маске uuid.ext (защита от path traversal). Файл, на который уже
    ссылается креатив, не удаляем — иначе сломаем открутку.
    """
    if not _UPLOAD_NAME_RE.match(name):
        raise HTTPException(400, "Некорректное имя файла")
    pool = await get_pool()
    rel = f"/uploads/{name}"
    used = await pool.fetchval(
        """
        SELECT 1 FROM creatives
        WHERE asset_url LIKE '%' || $1 OR video_url LIKE '%' || $1 OR native_icon_url LIKE '%' || $1
        LIMIT 1
        """,
        rel,
    )
    if used:
        raise HTTPException(409, "Файл уже используется креативом")
    from main import UPLOAD_DIR

    try:
        os.remove(os.path.join(UPLOAD_DIR, name))
    except FileNotFoundError:
        pass
    return None
