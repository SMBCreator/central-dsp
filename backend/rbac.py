"""RBAC: матрица 9 ролей + tenant scoping + пофлаговые permissions.

Роли (ТЗ):
  superadmin — всё, включая управление пользователями и системными настройками
  admin      — всё в рамках платформы (без системных операций)
  manager    — управление кампаниями/креативами/SSP/ОРД (свои сущности)
  advertiser — только свои кампании/креативы/статистика (tenant isolation)
  analyst    — read-only повсюду
  finance    — read-only + биллинговые операции (deposit/adjust)
  agency     — иерархия: создаёт клиентов, наценки
  client     — read-only по умолчанию; self-serve по флагам permissions
  viewer     — гостевой отчётный доступ (страница /report, scope из guest_access)

Пофлаговые permissions (только для роли client, admin-роли всегда True):
  can_edit_campaigns  — создание/правка кампаний, line items
  can_manage_creatives — создание/правка креативов
  can_view_billing    — просмотр биллинга (транзакции/summary)
  can_share_stats     — создание share-ссылок и гостевых аккаунтов
"""
from dataclasses import dataclass, replace

from fastapi import Depends, HTTPException

from auth import current_user

ALL_ROLES = ("superadmin", "admin", "manager", "advertiser", "analyst", "finance", "agency", "client", "viewer")
ADMIN_ROLES = ("superadmin", "admin")
# кто может создавать/менять кампании, креативы, line items и т.п.
CAMPAIGN_WRITE = ("superadmin", "admin", "manager", "advertiser", "agency")
# кто управляет SSP-партнёрами, правилами, ОРД-регистрациями
PLATFORM_WRITE = ("superadmin", "admin", "manager")
# кто проводит деньги
FINANCE_WRITE = ("superadmin", "admin", "finance")

# Пофлаговые permissions. Задаются на роль client; admin-роли/менеджеры имеют их неявно.
PERMISSION_FLAGS = ("can_edit_campaigns", "can_manage_creatives", "can_view_billing", "can_share_stats")

# admin-подобные роли, у которых все флаги неявно True (не читаем permissions из БД).
_FLAG_IMPLICIT_ROLES = ("superadmin", "admin", "manager", "advertiser", "agency")


def _normalized_permissions(user: dict) -> dict:
    """permissions из БД с обратной совместимостью старого ключа can_manage_billing -> can_view_billing."""
    perms = dict(user.get("permissions") or {})
    if "can_view_billing" not in perms and "can_manage_billing" in perms:
        perms["can_view_billing"] = bool(perms["can_manage_billing"])
    return perms


def has_permission(user: dict, flag: str) -> bool:
    """Есть ли у пользователя пофлаговое право.

    - admin-подобные роли: всегда True;
    - client: по флагу из users.permissions (с бэк-совместимым чтением can_manage_billing);
    - analyst/finance/viewer и прочие: False (write-права выдаются отдельными require_*).
    """
    role = user.get("role")
    if role in _FLAG_IMPLICIT_ROLES:
        return True
    if role == "client":
        return bool(_normalized_permissions(user).get(flag))
    return False


def require_roles(*roles: str):
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user
    return checker


require_campaign_write = require_roles(*CAMPAIGN_WRITE)
require_platform_write = require_roles(*PLATFORM_WRITE)
require_finance_write = require_roles(*FINANCE_WRITE)
require_admin = require_roles(*ADMIN_ROLES)


def is_admin(user: dict) -> bool:
    return user["role"] in ADMIN_ROLES


# Платформенный scope: видят объекты всех владельцев (analyst/finance — только
# чтение, это обеспечивают require_*-guards, а не scope).
PLATFORM_SCOPE_ROLES = ("superadmin", "admin", "analyst", "finance")


def can_see_all(user: dict) -> bool:
    """admin/superadmin/analyst/finance видят все сущности; manager/advertiser — только свои."""
    return user["role"] in PLATFORM_SCOPE_ROLES

# ---------- Иерархия (agency -> client), наценки, ограничения ----------

async def visible_user_ids(user: dict) -> list[str] | None:
    """None = видит всех. Для agency: свой id + все потомки. Для остальных не-админов: только свой."""
    if can_see_all(user):
        return None
    if user["role"] != "agency":
        return [str(user["id"])]
    from db import get_pool
    pool = await get_pool()
    rows = await pool.fetch(
        """
        WITH RECURSIVE tree AS (
            SELECT id FROM users WHERE id = $1
            UNION ALL
            SELECT u.id FROM users u JOIN tree t ON u.parent_user_id = t.id
        )
        SELECT id FROM tree
        """,
        user["id"],
    )
    return [str(r["id"]) for r in rows]


# ---------- Единый scope объектов (audit S-02/S-03) ----------
#
# Правило: list, detail, update, delete и billing одного пользователя фильтруют
# по одному и тому же Scope. Ссылочные поля в create/patch (group_id, pixel_id,
# counterparty_id, ...) должны принадлежать владельцу главного объекта — не
# «кому-то в scope» и не текущему пользователю (admin создаёт кампанию клиенту:
# группа тоже должна быть клиентской). Спецификация: docs/audit/scope_spec.md.


@dataclass(frozen=True)
class Scope:
    """Кого видит пользователь.

    all=True — платформенный scope (все владельцы). Иначе видны объекты
    владельцев из user_ids: manager/advertiser/client — только свои; agency —
    сам + всё поддерево клиентов; viewer — пусто (гость ходит только в отчёты,
    там его граница — advertiser_id из guest_access, см. report_scope_for).
    """

    all: bool
    user_ids: tuple[str, ...]
    advertiser_id: str | None = None
    spend_mult: float = 1.0

    def where(self, column: str, first_param: int) -> str:
        """SQL-предикат на два подряд идущих параметра: $first — bool «все»,
        $first+1 — uuid[] владельцев. Значения — self.params."""
        return f"(${first_param}::boolean OR {column} = ANY(${first_param + 1}::uuid[]))"

    @property
    def params(self) -> list:
        return [self.all, list(self.user_ids)]

    @property
    def owner_ids_param(self) -> list[str] | None:
        """Тот же scope одним параметром для отчётных запросов с $1::uuid[]:
        None = все владельцы (платформенный scope), иначе список. Пустой список —
        «никого» (не сентинел «все»): пустой scope не может раскрыть данные."""
        return None if self.all else list(self.user_ids)

    @staticmethod
    def owner_in(column: str, param: int = 1) -> str:
        """Предикат к owner_ids_param: `($n::uuid[] IS NULL OR column = ANY($n::uuid[]))`."""
        return f"(${param}::uuid[] IS NULL OR {column} = ANY(${param}::uuid[]))"

    def includes(self, owner_id) -> bool:
        return self.all or str(owner_id) in self.user_ids


async def scope_for(user: dict) -> Scope:
    """Scope CRUD-ручек. Единственная точка, где роль превращается в фильтр владельцев."""
    role = user.get("role")
    if role in PLATFORM_SCOPE_ROLES:
        return Scope(all=True, user_ids=(str(user["id"]),))
    if role == "viewer":
        return Scope(all=False, user_ids=())
    ids = await visible_user_ids(user)
    return Scope(all=False, user_ids=tuple(ids or (str(user["id"]),)))


async def report_scope_for(user: dict) -> Scope:
    """Scope отчётов/статистики: scope_for + граница гостя и множитель наценки.

    viewer: данные владельца (guest_access.owner_id), при заданном advertiser_id —
    только этот рекламодатель; spend — по snapshot наценки (F-02).
    """
    if user.get("role") == "viewer":
        from db import get_pool
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT owner_id, advertiser_id, markup_mult_snapshot, revoked_at FROM guest_access WHERE user_id = $1",
            user["id"],
        )
        if not row:
            raise HTTPException(status_code=403, detail="Гостевой доступ не настроен")
        if row["revoked_at"] is not None:
            raise HTTPException(status_code=403, detail="Доступ отозван")
        return Scope(
            all=False,
            user_ids=(str(row["owner_id"]),),
            advertiser_id=str(row["advertiser_id"]) if row["advertiser_id"] else None,
            spend_mult=float(row["markup_mult_snapshot"] or 1),
        )
    scope = await scope_for(user)
    mult = await spend_multiplier(str(user["id"]), user.get("role", ""))
    return replace(scope, spend_mult=mult)


# Таблицы с владельцем и предикат «владелец = …» для них. goals принадлежат
# владельцу пикселя. Ключи — литералы в коде, не пользовательский ввод.
_OWNED_TABLES: dict[str, str] = {
    "advertisers": "{owner}",
    "campaigns": "{owner}",
    "line_items": "{owner}",
    "creatives": "{owner}",
    "campaign_groups": "{owner}",
    "pixels": "{owner}",
    "goals": "pixel_id IN (SELECT id FROM pixels WHERE {owner})",
    "scoring_profiles": "{owner}",
    "counterparties": "{owner}",
    "ord_contracts": "{owner}",
    "rules": "{owner}",
    "audience_segments": "{owner}",
    "supply_partners": "{owner}",
}


def _owner_predicate(table: str, owner_sql: str) -> str:
    return _OWNED_TABLES[table].format(owner=owner_sql)


async def fetch_in_scope(conn, table: str, obj_id: str, scope: Scope, *, columns: str = "*",
                         for_update: bool = False):
    """Строка объекта, если он в scope пользователя, иначе None."""
    sql = (f"SELECT {columns} FROM {table} WHERE id = $1 AND "
           f"{_owner_predicate(table, scope.where('user_id', 2))}")
    if for_update:
        sql += " FOR UPDATE"
    return await conn.fetchrow(sql, obj_id, *scope.params)


async def assert_in_scope(conn, table: str, obj_id: str, scope: Scope, not_found: str, **kw):
    """Главный объект запроса (id в URL / родитель в body): 404, если вне scope.
    404, а не 403 — не раскрываем существование чужого объекта."""
    row = await fetch_in_scope(conn, table, obj_id, scope, **kw)
    if row is None:
        raise HTTPException(status_code=404, detail=not_found)
    return row


async def assert_owned_by(conn, table: str, obj_id: str, owner_id, not_found: str, *,
                          extra_where: str = "", extra_params: tuple = ()):
    """Ссылочное поле (group_id, pixel_id, counterparty_id, ...): объект должен
    принадлежать владельцу главного объекта. extra_where — дополнительные
    условия совместимости цепочки (нумерация параметров с $3)."""
    sql = f"SELECT * FROM {table} WHERE id = $1 AND {_owner_predicate(table, 'user_id = $2')}"
    if extra_where:
        sql += f" AND ({extra_where})"
    row = await conn.fetchrow(sql, obj_id, str(owner_id), *extra_params)
    if row is None:
        raise HTTPException(status_code=404, detail=not_found)
    return row


async def assert_refs_owned(conn, owner_id, refs: dict[str, str | None],
                            tables: dict[str, tuple[str, str]]) -> dict:
    """Проверка набора ссылочных полей body: refs {поле: id|None},
    tables {поле: (таблица, текст 404)}. Возвращает {поле: строка}."""
    rows = {}
    for field, value in refs.items():
        if value is None:
            continue
        table, not_found = tables[field]
        rows[field] = await assert_owned_by(conn, table, value, owner_id, not_found)
    return rows


async def effective_markup_pct(user_id: str) -> float:
    """Суммарная наценка по цепочке от пользователя вверх до корня.
    Наценки мультипликативны: (1+a)(1+b)-1."""
    from db import get_pool
    pool = await get_pool()
    rows = await pool.fetch(
        """
        WITH RECURSIVE chain AS (
            SELECT id, parent_user_id, markup_pct FROM users WHERE id = $1
            UNION ALL
            SELECT u.id, u.parent_user_id, u.markup_pct FROM users u JOIN chain c ON u.id = c.parent_user_id
        )
        SELECT markup_pct FROM chain
        """,
        user_id,
    )
    mult = 1.0
    for r in rows:
        mult *= 1.0 + float(r["markup_pct"] or 0) / 100.0
    return round((mult - 1.0) * 100.0, 4)


# --- Наценка как товарная цепочка (audit F-02) -------------------------------
# Роли, чей собственный кабинет показывает spend с наценкой по цепочке
# (остальные — internal/direct, видят net).
MARKUP_VIEW_ROLES = ("agency", "client")
# Роли, которые при передаче отчёта (share-ссылка, гость) могут добавить свой
# процент сверх своей цены. manager — его наценка задана admin-ом в
# users.markup_pct; advertiser/client/viewer — конечные получатели.
MARKUP_ADD_ROLES = ("superadmin", "admin", "agency")


async def spend_multiplier(user_id: str, role: str) -> float:
    """Цена, которую видит пользователь: net × это. Единственный источник для
    stats._resolve_scope и для snapshot на share/guest; зеркало — backfill в
    migrations/044 (CASE role IN ('agency','client'))."""
    if role in MARKUP_VIEW_ROLES:
        return 1.0 + (await effective_markup_pct(str(user_id))) / 100.0
    return 1.0


def share_multiplier(creator_mult: float, extra_pct: float) -> float:
    """Snapshot для получателя: цена создателя × (1 + его добавка)."""
    return round(float(creator_mult) * (1.0 + float(extra_pct or 0) / 100.0), 6)


def client_can_edit(user: dict) -> bool:
    """client по умолчанию read-only; self-serve включается флагом can_edit_campaigns."""
    if user["role"] != "client":
        return True
    return bool((user.get("permissions") or {}).get("can_edit_campaigns"))


def require_campaign_edit():
    """CAMPAIGN_WRITE-роли + client с флагом can_edit_campaigns."""
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] in CAMPAIGN_WRITE:
            return user
        if user["role"] == "client" and client_can_edit(user):
            return user
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return checker


require_campaign_editor = require_campaign_edit()


def _require_flag(flag: str, extra_roles: tuple[str, ...] = ()):
    """Dependency: admin-подобные роли + client с указанным флагом (+ extra_roles)."""
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] in extra_roles or has_permission(user, flag):
            return user
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return checker


# Создание/отзыв share-ссылок и гостевых аккаунтов:
# superadmin/admin/manager/agency/advertiser — всегда; client — по can_share_stats; остальные 403.
require_share_manager = _require_flag("can_share_stats")

# Управление креативами: те же write-роли + client с can_manage_creatives.
require_creative_manager = _require_flag("can_manage_creatives")

# Настройки аккаунта (fx-наценка, domain-lists): read-only роли (analyst/finance/viewer) — 403;
# client — только с can_edit_campaigns (настройки влияют на закупку).
require_settings_write = _require_flag("can_edit_campaigns")


def can_view_billing(user: dict) -> bool:
    """Просмотр биллинга: finance/analyst видят всё (read-only роли аналитики денег),
    admin-подобные — всегда, client — по флагу can_view_billing, viewer — нет."""
    if user["role"] in ("analyst", "finance"):
        return True
    return has_permission(user, "can_view_billing")


def require_billing_view():
    async def checker(user: dict = Depends(current_user)) -> dict:
        if can_view_billing(user):
            return user
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return checker


require_billing_viewer = require_billing_view()
