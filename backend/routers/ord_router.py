"""ОРД-модуль: контрагенты, договоры, регистрация креативов (erid), выгрузки.

Все мутации идут через get_ord_adapter(): с ключом ORD_OZON_API_KEY — реальный
Ozon-клиент, без ключа — mock (dev). Статусы храним в БД, повторная
регистрация уже registered-сущности не выполняется (идемпотентность).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from ad_market import assert_campaign_not_foreign, foreign_campaign_sql, foreign_counterparty_sql
from auth import current_user
from rbac import assert_in_scope, assert_owned_by, require_platform_write, scope_for
from db import get_pool
from ord import OrdError, OrdNotConfigured, get_ord_adapter
from ord import invoices as ord_invoices
from ord.adapter import OrdDataError
from ord.platform import ensure_platform, load_platform, org_payload, platform_status
from ord.chain import assert_counterparty_ref
from ord import notifications as ord_notifications
from ord.service import register_creative as ord_register_creative

router = APIRouter(prefix="/ord", tags=["ord"])


# ---------------------------------------------------------------- schemas --
class CounterpartyIn(BaseModel):
    name: str
    inn: str = Field(min_length=5, max_length=30)
    ogrn: Optional[str] = Field(default=None, max_length=20)
    legal_type: str = "ul"           # ul | ip | fl | foreign
    role: str = "advertiser"         # advertiser | agency | publisher | ors
    advertiser_id: Optional[str] = None
    address: Optional[str] = Field(default=None, max_length=500)  # местонахождение (юрадрес)
    # Наша организация (ОРС): isOpc в ОРД, владелец сводной площадки. Ровно одна на платформу.
    is_self: bool = False
    rs_url: Optional[str] = Field(default=None, max_length=500)   # URL рекламной системы (rsUrl)
    # C30: иностранное ЮЛ (legal_type=foreign) — страна по ОКСМ + налоговый/рег. номер
    region_number: Optional[str] = Field(default=None, pattern=r"^\d{3}$")
    tax_id: Optional[str] = Field(default=None, max_length=64)
    registration_number: Optional[str] = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _foreign_requisites(self):
        if self.legal_type != "foreign":
            # Реквизиты иностранца у резидента РФ не храним — ОРД их отклонит.
            self.region_number = self.tax_id = self.registration_number = None
            return self
        from ord.ozon_mapping import foreign_requisites
        from ord.adapter import OrdDataError
        self.tax_id = (self.tax_id or "").strip() or None
        self.registration_number = (self.registration_number or "").strip() or None
        try:
            foreign_requisites(self.model_dump())
        except OrdDataError as e:
            raise ValueError(str(e).split(": ", 1)[-1])
        return self


class ContractIn(BaseModel):
    client_id: str
    contractor_id: str
    contract_type: str = "service"
    subject_type: str = "distribution"
    number: str
    signed_at: str                   # YYYY-MM-DD
    amount: Optional[float] = None
    is_vat: bool = True
    parent_contract_id: Optional[str] = None


class CreativeRegisterIn(BaseModel):
    # Пусто → договор подбирается по контрагенту креатива (ord.service.resolve_contract)
    contract_id: Optional[str] = None


class InvoiceItemIn(BaseModel):
    creative_id: str
    version: int
    period: str                      # YYYY-MM — строго один календарный месяц
    impressions: int | None = None
    clicks: int | None = None
    spend: float | None = None    # цена клиента
    unit_cost: float | None = None
    payment_type: str | None = None  # cpm | cpc


class InvoiceContractIn(BaseModel):
    initial_contract_id: str
    amount_gross: float


class InvoiceIn(BaseModel):
    contract_id: str                 # исполнительский договор (ОРС-модель, B13e)
    number: str = Field(min_length=1, max_length=64)
    invoice_date: str
    period_start: str
    period_end: str
    amount_gross: float | None = None   # с НДС; пусто → Σ spend строк
    amount: float | None = None         # устаревшее имя amount_gross (B13b)
    vat_rate: int | None = None         # пусто → 22 (0 при is_vat=false)
    is_vat: bool | None = None
    client_role: str | None = None
    contractor_role: str | None = None
    campaign_ids: list[str] = Field(default_factory=list)
    contracts: list[InvoiceContractIn] = Field(default_factory=list)  # разбивка по изначальным договорам
    items: list[InvoiceItemIn]


def _row(r) -> dict:
    return dict(r) if r else None


# ----------------------------------------------------------- counterparties --
@router.get("/counterparties")
async def list_counterparties(user: dict = Depends(current_user)):
    pool = await get_pool()
    scope = await scope_for(user)
    # Наша организация (ОРС) видна всем, у кого есть свой scope: она — исполнитель
    # исполнительского договора любого клиента (ord_spec §7 D3). Viewer (пустой
    # scope) не видит ничего.
    self_visible = scope.all or bool(scope.user_ids)
    # ТЗ п.5: юрлица рекламодателей «за пределами РФ» в «Маркировке» не показываются.
    rows = await pool.fetch(
        f"SELECT * FROM counterparties WHERE ({scope.where('user_id', 1)} OR (is_self AND $3::bool)) "
        f"AND NOT {foreign_counterparty_sql('counterparties.id')} "
        "ORDER BY created_at DESC",
        *scope.params, self_visible,
    )
    return [dict(r) for r in rows]


@router.post("/counterparties", status_code=201)
async def create_counterparty(body: CounterpartyIn, user: dict = Depends(require_platform_write)):
    if body.legal_type not in ("ul", "ip", "fl", "foreign"):
        raise HTTPException(400, "legal_type: ul | ip | fl | foreign")
    if body.legal_type in ("ul",) and len(body.inn) != 10 or body.legal_type in ("ip", "fl") and len(body.inn) != 12:
        if body.legal_type != "foreign":
            raise HTTPException(400, "ИНН: 10 цифр для юрлица, 12 для ИП/физлица")
    pool = await get_pool()
    # Контрагент, привязанный к рекламодателю, принадлежит владельцу рекламодателя
    # (admin заводит его клиенту); без привязки — текущему пользователю.
    owner = str(user["id"])
    if body.advertiser_id:
        adv = await assert_in_scope(
            pool, "advertisers", body.advertiser_id, await scope_for(user), "Рекламодатель не найден",
            columns="id, user_id",
        )
        owner = str(adv["user_id"])
    role = body.role
    if body.is_self:
        # Наша организация — платформенная сущность: только superadmin/admin, роль всегда ОРС.
        if user["role"] not in ("superadmin", "admin"):
            raise HTTPException(403, "Отметить «Наша организация (ОРС)» может только администратор")
        role = "ors"
    async with pool.acquire() as conn, conn.transaction():
        if body.is_self:
            await conn.execute("UPDATE counterparties SET is_self = false, updated_at = now() WHERE is_self AND inn <> $1", body.inn)
        row = await conn.fetchrow(
            """INSERT INTO counterparties (user_id, advertiser_id, name, inn, ogrn, legal_type, role, address, is_self, rs_url,
                                          region_number, tax_id, registration_number)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
               ON CONFLICT (user_id, inn, role) DO UPDATE
                 SET name = EXCLUDED.name, ogrn = COALESCE(EXCLUDED.ogrn, counterparties.ogrn),
                     region_number = COALESCE(EXCLUDED.region_number, counterparties.region_number),
                     tax_id = COALESCE(EXCLUDED.tax_id, counterparties.tax_id),
                     registration_number = COALESCE(EXCLUDED.registration_number, counterparties.registration_number),
                     address = COALESCE(EXCLUDED.address, counterparties.address),
                     is_self = counterparties.is_self OR EXCLUDED.is_self,
                     rs_url = COALESCE(EXCLUDED.rs_url, counterparties.rs_url),
                     ord_status = CASE WHEN counterparties.ord_status IN ('registered', 'erir_error')
                                        AND (counterparties.is_self <> (counterparties.is_self OR EXCLUDED.is_self)
                                             OR counterparties.rs_url IS DISTINCT FROM COALESCE(EXCLUDED.rs_url, counterparties.rs_url)
                                             OR counterparties.region_number IS DISTINCT FROM COALESCE(EXCLUDED.region_number, counterparties.region_number)
                                             OR counterparties.tax_id IS DISTINCT FROM COALESCE(EXCLUDED.tax_id, counterparties.tax_id)
                                             OR counterparties.registration_number IS DISTINCT FROM COALESCE(EXCLUDED.registration_number, counterparties.registration_number))
                                       THEN 'pending' ELSE counterparties.ord_status END,
                     updated_at = now()
               RETURNING *""",
            owner, body.advertiser_id, body.name, body.inn, body.ogrn, body.legal_type, role, body.address,
            body.is_self, body.rs_url, body.region_number, body.tax_id, body.registration_number,
        )
    return _row(row)


async def _adapter_or_503():
    """Production без ключа ОРД — 503 с инструкцией, не 500 (audit L-01)."""
    try:
        return await get_ord_adapter()
    except OrdNotConfigured as e:
        raise HTTPException(503, str(e))


@router.post("/counterparties/{cp_id}/register")
async def register_counterparty(cp_id: str, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    cp = await assert_in_scope(pool, "counterparties", cp_id, await scope_for(user), "Контрагент не найден")
    owner = str(cp["user_id"])
    if cp["ord_status"] == "registered":
        return _row(cp)  # идемпотентно
    adapter = await _adapter_or_503()
    try:
        # external_id = наш UUID: в ОРД это ключ upsert, повторная отправка
        # обновляет карточку организации, а не создаёт дубль.
        # Наша организация уходит вместе со сводной площадкой (если та уже зарегистрирована),
        # иначе ОРД потеряет признак владельца при обновлении карточки.
        out = await adapter.register_counterparty(
            org_payload(cp, await load_platform(pool) if cp.get("is_self") else None)
        )
        row = await pool.fetchrow(
            """UPDATE counterparties SET ord_external_id=$3, ord_status='registered',
               ord_error=NULL, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *""",
            cp_id, owner, out["external_id"],
        )
        # B13g: ОРД принял повторно — открытые ошибки ЕРИР по организации закрыты
        await ord_notifications.resolve_for(pool, "organisation", str(cp_id))
        return _row(row)
    except OrdError as e:
        await pool.execute(
            "UPDATE counterparties SET ord_status='failed', ord_error=$3, updated_at=now() WHERE id=$1 AND user_id=$2",
            cp_id, owner, str(e),
        )
        raise HTTPException(502, f"ОРД: {e}")


# ------------------------------------------------------- ERIR notifications --
@router.get("/notifications")
async def list_notifications(user: dict = Depends(current_user)):
    """B13g: открытые ошибки ЕРИР (объект принят ОРД, отклонён ЕРИР) в scope
    пользователя. Закрываются автоматически успешной повторной регистрацией объекта."""
    pool = await get_pool()
    scope = await scope_for(user)
    async with pool.acquire() as conn:
        items = await ord_notifications.list_open(conn, scope)
    return {"items": items, "total": len(items)}


@router.post("/notifications/sync")
async def sync_notifications_now(user: dict = Depends(require_platform_write)):
    """Забрать ошибки ЕРИР из ОРД сейчас (то же, что cron /ord-notifications)."""
    pool = await get_pool()
    try:
        adapter = await get_ord_adapter()
        return await ord_notifications.sync_notifications(pool, adapter)
    except OrdError as e:
        raise HTTPException(502, f"ОРД: {e}")


# ------------------------------------------------------------------ platform --
@router.get("/platform")
async def get_platform(user: dict = Depends(current_user)):
    """Сводная площадка ОРС: URL, наша организация, что зарегистрировано, чего не хватает."""
    return await platform_status(await get_pool())


@router.post("/platform/register")
async def register_platform(user: dict = Depends(require_platform_write)):
    """Регистрирует площадку central-dsp и привязывает к нашей организации (isPlatformOwner)."""
    if user["role"] not in ("superadmin", "admin"):
        raise HTTPException(403, "Регистрация площадки доступна только администратору")
    adapter = await _adapter_or_503()
    try:
        return await ensure_platform(await get_pool(), adapter, force=True)
    except OrdDataError as e:
        raise HTTPException(422, str(e))
    except OrdError as e:
        raise HTTPException(502, f"ОРД: {e}")


# ---------------------------------------------------------------- contracts --
@router.get("/contracts")
async def list_contracts(user: dict = Depends(current_user)):
    pool = await get_pool()
    scope = await scope_for(user)
    rows = await pool.fetch(
        f"""SELECT c.*, cl.name AS client_name, ct.name AS contractor_name
           FROM ord_contracts c
           JOIN counterparties cl ON cl.id = c.client_id
           JOIN counterparties ct ON ct.id = c.contractor_id
           WHERE {scope.where("c.user_id", 1)}
             AND NOT {foreign_counterparty_sql("c.client_id")}
           ORDER BY c.created_at DESC""",
        *scope.params,
    )
    return [dict(r) for r in rows]


@router.post("/contracts", status_code=201)
async def create_contract(body: ContractIn, user: dict = Depends(require_platform_write)):
    from datetime import date

    pool = await get_pool()
    scope = await scope_for(user)
    # Договор принадлежит владельцу контрагентов; обе стороны — одного владельца,
    # родительский договор — его же (чужой договор в цепочке = чужие данные в фиде ОРД).
    client = await assert_in_scope(pool, "counterparties", body.client_id, scope, "Контрагент не найден",
                                   columns="id, user_id")
    owner = str(client["user_id"])
    # Исполнитель — контрагент владельца либо наша организация (ОРС):
    # исполнительский договор клиента заключается именно с ней.
    await assert_counterparty_ref(pool, body.contractor_id, owner)
    if body.parent_contract_id:
        await assert_owned_by(pool, "ord_contracts", body.parent_contract_id, owner, "Договор не найден")
    row = await pool.fetchrow(
        """INSERT INTO ord_contracts (user_id, client_id, contractor_id, contract_type,
             subject_type, number, signed_at, amount, is_vat, parent_contract_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *""",
        owner, body.client_id, body.contractor_id, body.contract_type,
        body.subject_type, body.number, date.fromisoformat(body.signed_at),
        body.amount, body.is_vat, body.parent_contract_id,
    )
    return _row(row)


@router.post("/contracts/{contract_id}/register")
async def register_contract(contract_id: str, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    c = await assert_in_scope(pool, "ord_contracts", contract_id, await scope_for(user), "Договор не найден")
    owner = str(c["user_id"])
    if c["ord_status"] == "registered":
        return _row(c)
    # обе стороны должны быть зарегистрированы в ОРД — договор ссылается на их внешние ID
    sides: dict[str, str] = {}
    for side in ("client_id", "contractor_id"):
        cp = await pool.fetchrow("SELECT ord_status, ord_external_id FROM counterparties WHERE id = $1", c[side])
        if not cp or cp["ord_status"] != "registered" or not cp["ord_external_id"]:
            raise HTTPException(409, "Сначала зарегистрируйте обоих контрагентов в ОРД")
        sides[side] = cp["ord_external_id"]
    parent_external_id = None
    if c["parent_contract_id"]:
        parent_external_id = await pool.fetchval(
            "SELECT ord_external_id FROM ord_contracts WHERE id = $1 AND ord_status IN ('registered', 'erir_error')",
            c["parent_contract_id"],
        )
        if not parent_external_id:
            raise HTTPException(409, "Сначала зарегистрируйте родительский договор в ОРД")
    adapter = await _adapter_or_503()
    try:
        out = await adapter.register_contract(
            {
                "external_id": str(c["id"]),
                "client_external_id": sides["client_id"],
                "contractor_external_id": sides["contractor_id"],
                "parent_external_id": parent_external_id,
                "number": c["number"], "signed_at": str(c["signed_at"]),
                "contract_type": c["contract_type"], "subject_type": c["subject_type"],
                "amount": str(c["amount"] or 0), "is_vat": c["is_vat"],
            }
        )
        row = await pool.fetchrow(
            """UPDATE ord_contracts SET ord_external_id=$3, ord_status='registered',
               ord_error=NULL, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *""",
            contract_id, owner, out["external_id"],
        )
        await ord_notifications.resolve_for(pool, "contract", str(contract_id))
        return _row(row)
    except OrdError as e:
        await pool.execute(
            "UPDATE ord_contracts SET ord_status='failed', ord_error=$3, updated_at=now() WHERE id=$1 AND user_id=$2",
            contract_id, owner, str(e),
        )
        raise HTTPException(502, f"ОРД: {e}")


# ---------------------------------------------------------------- creatives --
@router.post("/creatives/{creative_id}/register")
async def register_creative(creative_id: str, body: CreativeRegisterIn, user: dict = Depends(require_platform_write)):
    """Ручная регистрация креатива в ОРД (страница «Маркировка»). Тот же код,
    что авторегистрация при approve — ord.service.register_creative."""
    pool = await get_pool()
    cr = await assert_in_scope(pool, "creatives", creative_id, await scope_for(user), "Креатив не найден")
    await assert_campaign_not_foreign(pool, cr["campaign_id"])
    if body.contract_id:
        # Договор — того же владельца, что и креатив (не текущего пользователя).
        await assert_owned_by(pool, "ord_contracts", body.contract_id, cr["user_id"], "Договор не найден")
    result = await ord_register_creative(pool, dict(cr), contract_id=body.contract_id)
    if result["outcome"] == "awaiting_contract":
        raise HTTPException(409, result.get("error") or "Договор должен быть зарегистрирован в ОРД до регистрации креатива")
    if result["outcome"] == "awaiting_kktu":
        raise HTTPException(409, result.get("error") or "Укажите код ККТУ креатива или кампании до регистрации в ОРД")
    if result["outcome"] == "failed":
        raise HTTPException(502, f"ОРД: {result.get('error')}")
    return {k: v for k, v in result.items() if k != "outcome"}


@router.get("/creatives")
async def list_ord_creatives(user: dict = Depends(current_user)):
    pool = await get_pool()
    scope = await scope_for(user)
    rows = await pool.fetch(
        f"""SELECT oc.*, c.name AS creative_name, c.campaign_id, c.ord_locked_at,
                  (oc.superseded_at IS NULL) AS is_current
           FROM ord_creatives oc JOIN creatives c ON c.id = oc.creative_id
           WHERE {scope.where("c.user_id", 1)}
             AND NOT {foreign_campaign_sql("c.campaign_id")}
           ORDER BY oc.creative_id, oc.version DESC, oc.created_at DESC""",
        *scope.params,
    )
    return [dict(r) for r in rows]


@router.get("/creatives/{creative_id}/versions")
async def creative_versions(creative_id: str, user: dict = Depends(current_user)):
    """История маркировки креатива: все версии с erid и интервалами действия."""
    pool = await get_pool()
    await assert_in_scope(pool, "creatives", creative_id, await scope_for(user), "Креатив не найден")
    rows = await pool.fetch(
        """SELECT id, version, erid, status, ord_provider, ord_external_id, contract_id,
                  registered_at, superseded_at, error, (superseded_at IS NULL) AS is_current
             FROM ord_creatives WHERE creative_id = $1 ORDER BY version""",
        creative_id,
    )
    return [dict(r) for r in rows]


# ----------------------------------------------------------------- invoices --
# Акты (B13b): один договор + период + сумма; строки — (креатив, версия erid,
# месяц). Логика в ord/invoices.py, здесь только scope и HTTP-коды.
def _inv_http(e: ord_invoices.InvoiceError) -> HTTPException:
    return HTTPException(e.status, e.detail)


async def _contract_for_invoice(pool, user: dict, contract_id: str, campaign_ids: list[str]) -> dict:
    """Договор в scope пользователя; кампании фильтра — того же владельца."""
    c = await assert_in_scope(pool, "ord_contracts", contract_id, await scope_for(user), "Договор не найден")
    for cid in campaign_ids:
        await assert_owned_by(pool, "campaigns", cid, c["user_id"], "Кампания не найдена")
    return dict(c)


@router.get("/invoices/preview")
async def preview_invoice(
    contract_id: str,
    period_start: str,
    period_end: str,
    campaign_ids: list[str] = Query(default_factory=list),
    invoice_id: str | None = None,  # при редактировании: строки этого акта не считаются «занятыми»
    user: dict = Depends(current_user),
):
    """Строки акта по факту за период: показы/клики/расход (цена клиента) по
    (креатив, версия erid, месяц) + предупреждения (уже в другом акте, без erid)."""
    pool = await get_pool()
    contract = await _contract_for_invoice(pool, user, contract_id, campaign_ids)
    if invoice_id:
        await assert_owned_by(pool, "ord_invoices", invoice_id, contract["user_id"], "Акт не найден")
    try:
        return await ord_invoices.preview(
            pool, contract,
            ord_invoices.parse_date(period_start, "period_start"),
            ord_invoices.parse_date(period_end, "period_end"),
            campaign_ids, exclude_invoice_id=invoice_id,
        )
    except ord_invoices.InvoiceError as e:
        raise _inv_http(e)


@router.get("/invoices")
async def list_invoices(user: dict = Depends(current_user)):
    pool = await get_pool()
    scope = await scope_for(user)
    rows = await pool.fetch(
        f"""SELECT i.*, c.number AS contract_number,
                   (SELECT count(*) FROM ord_invoice_items it WHERE it.invoice_id = i.id) AS items_count,
                   (SELECT count(*) FROM ord_invoice_items it WHERE it.invoice_id = i.id AND it.status = 'accepted') AS items_accepted,
                   (SELECT count(*) FROM ord_invoice_items it WHERE it.invoice_id = i.id AND it.status = 'rejected') AS items_rejected
              FROM ord_invoices i JOIN ord_contracts c ON c.id = i.contract_id
             WHERE {scope.where("i.user_id", 1)}
             ORDER BY i.created_at DESC""",
        *scope.params,
    )
    return [dict(r) for r in rows]


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    await assert_in_scope(pool, "ord_invoices", invoice_id, await scope_for(user), "Акт не найден", columns="id")
    return await ord_invoices.load(pool, invoice_id)


@router.post("/invoices", status_code=201)
async def create_invoice(body: InvoiceIn, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    contract = await _contract_for_invoice(pool, user, body.contract_id, body.campaign_ids)
    try:
        return await ord_invoices.save(
            pool, invoice_id=None, contract=contract, data=body.model_dump(exclude={"items"}),
            items=[it.model_dump() for it in body.items], actor_id=str(user["id"]),
        )
    except ord_invoices.InvoiceError as e:
        raise _inv_http(e)


@router.put("/invoices/{invoice_id}")
async def update_invoice(invoice_id: str, body: InvoiceIn, user: dict = Depends(require_platform_write)):
    """Редактирование (в т.ч. после отправки): статус снова draft, повторный
    submit = коррекция акта в ОРД. Договор акта менять нельзя."""
    pool = await get_pool()
    inv = await assert_in_scope(pool, "ord_invoices", invoice_id, await scope_for(user), "Акт не найден")
    if str(inv["contract_id"]) != body.contract_id:
        raise HTTPException(409, "Договор акта изменить нельзя — создайте новый акт")
    if inv["status"] == "submitting":
        raise HTTPException(409, "Акт отправляется в ОРД, дождитесь результата")
    contract = await _contract_for_invoice(pool, user, body.contract_id, body.campaign_ids)
    try:
        return await ord_invoices.save(
            pool, invoice_id=invoice_id, contract=contract, data=body.model_dump(exclude={"items"}),
            items=[it.model_dump() for it in body.items], actor_id=str(user["id"]),
        )
    except ord_invoices.InvoiceError as e:
        raise _inv_http(e)


@router.delete("/invoices/{invoice_id}", status_code=204)
async def delete_invoice(invoice_id: str, user: dict = Depends(require_platform_write)):
    """Удалить можно только неотправленный черновик: отправленный акт в ОРД
    уже существует, его правят повторной отправкой."""
    pool = await get_pool()
    inv = await assert_in_scope(pool, "ord_invoices", invoice_id, await scope_for(user), "Акт не найден")
    if inv["ord_external_id"] or inv["status"] not in ("draft", "failed"):
        raise HTTPException(409, "Отправленный в ОРД акт удалить нельзя")
    await pool.execute("DELETE FROM ord_invoices WHERE id = $1", invoice_id)


@router.post("/invoices/{invoice_id}/submit")
async def submit_invoice(invoice_id: str, user: dict = Depends(require_platform_write)):
    """invoice → link creatives → statistic по месяцам; построчный статус.
    503 — ОРД не настроен (production без ключа), 502 — акт целиком отклонён."""
    pool = await get_pool()
    inv = await assert_in_scope(pool, "ord_invoices", invoice_id, await scope_for(user), "Акт не найден")
    await _adapter_or_503()
    try:
        result = await ord_invoices.submit(pool, dict(inv))
    except ord_invoices.InvoiceError as e:
        raise _inv_http(e)
    if result["status"] == "failed":
        raise HTTPException(502, f"ОРД: {result.get('error')}")
    return result


# ------------------------------------------------------------------ exports --
@router.get("/exports")
async def list_exports(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch("SELECT * FROM ord_export_log ORDER BY started_at DESC LIMIT 50")
    return [dict(r) for r in rows]


@router.get("/exports/{log_id}/rows")
async def export_rows(log_id: str, user: dict = Depends(current_user)):
    """Построчный результат выгрузки: что ОРД принял, что отклонил и почему."""
    pool = await get_pool()
    scope = await scope_for(user)
    rows = await pool.fetch(
        f"""SELECT r.*, c.name AS creative_name, c.campaign_id
              FROM ord_export_rows r
              LEFT JOIN creatives c ON c.id = r.creative_id
             WHERE r.log_id = $1 AND (c.id IS NULL OR {scope.where("c.user_id", 2)})
             ORDER BY r.status DESC, r.impressions DESC""",
        log_id, *scope.params,
    )
    return [dict(r) for r in rows]
