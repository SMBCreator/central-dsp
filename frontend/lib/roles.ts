/**
 * Матрица доступа ролей к разделам кабинета (зеркалит серверный RBAC backend/rbac.py).
 *
 * Роли:
 *   superadmin/admin — всё; manager — всё кроме системных настроек;
 *   agency — свои клиенты и их кампании; advertiser — свои сущности;
 *   analyst — read-only аналитика; finance — read-only + биллинг;
 *   client — read-only, self-serve по флагам permissions; viewer — только /report.
 *
 * Сервер — источник истины: фронтовая матрица только скрывает недоступное из UI,
 * каждый write-эндпоинт дополнительно гейтится на бэке.
 */

export type Role =
  | "superadmin"
  | "admin"
  | "manager"
  | "advertiser"
  | "analyst"
  | "finance"
  | "agency"
  | "client"
  | "viewer"

export type Permissions = {
  can_edit_campaigns?: boolean
  can_manage_creatives?: boolean
  can_view_billing?: boolean
  can_share_stats?: boolean
}

export type PermissionFlag = keyof Permissions

const ALL: Role[] = [
  "superadmin",
  "admin",
  "manager",
  "advertiser",
  "analyst",
  "finance",
  "agency",
  "client",
  "viewer",
]
const ADMINS: Role[] = ["superadmin", "admin"]
const PLATFORM: Role[] = ["superadmin", "admin", "manager"]
const BUYERS: Role[] = ["superadmin", "admin", "manager", "advertiser", "agency", "client"]
const BUYERS_RO: Role[] = [...BUYERS, "analyst"]

/**
 * Раздел -> роли, которым он виден. Маршруты сверяются по префиксу.
 * Порядок важен: более специфичные префиксы должны идти раньше "/".
 */
export const ROUTE_ACCESS: Array<{ prefix: string; roles: Role[] }> = [
  { prefix: "/report", roles: ["viewer"] },
  { prefix: "/profile", roles: ALL },
  { prefix: "/analytics", roles: [...BUYERS_RO, "finance"] },
  { prefix: "/campaigns", roles: BUYERS_RO },
  { prefix: "/creatives", roles: BUYERS_RO },
  { prefix: "/moderation", roles: ADMINS },
  { prefix: "/scoring", roles: BUYERS_RO },
  { prefix: "/advertisers", roles: PLATFORM },
  { prefix: "/supply", roles: PLATFORM },
  { prefix: "/rtb-test", roles: PLATFORM },
  { prefix: "/domains", roles: ADMINS },
  { prefix: "/billing", roles: [...BUYERS, "analyst", "finance"] },
  { prefix: "/ord", roles: PLATFORM },
  { prefix: "/pixels", roles: BUYERS_RO },
  { prefix: "/audiences", roles: BUYERS_RO },
  { prefix: "/rules", roles: PLATFORM },
  { prefix: "/ai", roles: BUYERS },
  { prefix: "/users", roles: [...ADMINS, "agency"] },
  { prefix: "/access", roles: ["superadmin", "admin", "manager", "advertiser", "agency", "client"] },
  { prefix: "/settings", roles: ADMINS },
  { prefix: "/audit", roles: ADMINS },
  { prefix: "/admin", roles: ["superadmin"] },
  { prefix: "/", roles: ALL.filter((r) => r !== "viewer") },
]

/** Разделы, где доступ client дополнительно зависит от флага permissions. */
const CLIENT_FLAG_ROUTES: Record<string, keyof Permissions> = {
  "/billing": "can_view_billing",
  "/access": "can_share_stats",
}

function matchRoute(pathname: string): { prefix: string; roles: Role[] } | undefined {
  return ROUTE_ACCESS.find(
    (r) => r.prefix !== "/" && (pathname === r.prefix || pathname.startsWith(r.prefix + "/")),
  ) ?? ROUTE_ACCESS.find((r) => r.prefix === "/")
}

export function canAccessRoute(
  role: string | null | undefined,
  pathname: string,
  permissions?: Permissions | null,
): boolean {
  if (!role) return false
  const rule = matchRoute(pathname)
  if (!rule) return true
  if (!rule.roles.includes(role as Role)) return false
  if (role === "client") {
    const flag = CLIENT_FLAG_ROUTES[rule.prefix]
    if (flag) return !!permissions?.[flag]
  }
  return true
}

/** Стартовая страница после логина / редирект с недоступного раздела. */
export function homeRoute(role: string | null | undefined): string {
  if (role === "viewer") return "/report"
  if (role === "finance") return "/billing"
  return "/"
}

export type WriteSection = "campaigns" | "creatives" | "billing" | "share" | "settings"

/**
 * Можно ли показывать write-действия (создать/изменить/удалить/депозит) в разделе.
 * Зеркалит серверные require_*: сервер всё равно вернёт 403 при рассинхроне.
 */
export function canWrite(
  role: string | null | undefined,
  section: WriteSection,
  permissions?: Permissions | null,
): boolean {
  if (!role) return false
  const r = role as Role
  if (r === "viewer" || r === "analyst") return false
  if (r === "finance") return section === "billing"
  if (r === "client") {
    switch (section) {
      case "campaigns":
        return !!permissions?.can_edit_campaigns
      case "creatives":
        return !!permissions?.can_manage_creatives
      case "share":
        return !!permissions?.can_share_stats
      case "billing":
      case "settings":
        return false
    }
  }
  // здесь r — одна из write-ролей (finance/analyst/viewer/client уже обработаны выше)
  if (section === "billing") return ADMINS.includes(r)
  if (section === "settings") return ADMINS.includes(r)
  return true
}

// ---- Обратная совместимость со старым API (app-shell/sidebar до рефактора) ----

/** Разделы только для админов и менеджеров платформы */
/** F-02: кто может добавить свой процент сверх своей цены при выдаче отчёта (share-ссылка, гость).
 *  Зеркало backend rbac.MARKUP_ADD_ROLES. */
export const MARKUP_ADD_ROLES: Role[] = ["superadmin", "admin", "agency"]

export function canAddShareMarkup(role: string | null | undefined): boolean {
  return MARKUP_ADD_ROLES.includes(role as Role)
}

export const ADMIN_ROUTES = ["/supply", "/rtb-test", "/users", "/access", "/settings", "/advertisers", "/audit"] as const

export function isAdminRole(role: string | null | undefined): boolean {
  return role === "admin" || role === "manager" || role === "superadmin"
}

export function isAdminRoute(pathname: string): boolean {
  return ADMIN_ROUTES.some((r) => pathname === r || pathname.startsWith(r + "/"))
}
