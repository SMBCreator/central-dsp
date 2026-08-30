/**
 * Кабинет рядового пользователя: админские разделы скрываются из меню
 * и блокируются по прямой ссылке (редирект на дашборд).
 *
 * Админские роли — admin и manager (сотрудники платформы).
 * Рядовые — advertiser, client, analyst, viewer: видят всё,
 * кроме служебных разделов платформы.
 */

/** Разделы только для админов и менеджеров платформы */
export const ADMIN_ROUTES = [
  "/supply", // SSP-партнёры
  "/rtb-test", // RTB-тестер
  "/users", // Пользователи и доступы
  "/access", // Доступ клиентов
  "/settings", // Настройки платформы
  "/advertisers", // Управление рекламодателями
] as const

export function isAdminRole(role: string | null | undefined): boolean {
  return role === "admin" || role === "manager"
}

export function isAdminRoute(pathname: string): boolean {
  return ADMIN_ROUTES.some((r) => pathname === r || pathname.startsWith(r + "/"))
}
