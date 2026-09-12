/**
 * Адреса бизнес-уведомлений (audit C4b / N-03, N-04). Зеркалит серверную
 * нормализацию notify_business.normalise_emails: strip/lower/уникальные,
 * не больше 20, невалидные — отдельным списком для подсветки в форме.
 */
export const NOTIFY_EMAILS_MAX = 20
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

export type ParsedEmails = { emails: string[]; invalid: string[]; tooMany: boolean }

/** Текст поля (через запятую, точку с запятой, пробел или перенос строки) -> адреса. */
export function parseEmails(text: string): ParsedEmails {
  const emails: string[] = []
  const invalid: string[] = []
  for (const raw of text.split(/[\s,;]+/)) {
    const addr = raw.trim().toLowerCase()
    if (!addr) continue
    if (!EMAIL_RE.test(addr)) {
      if (!invalid.includes(addr)) invalid.push(addr)
      continue
    }
    if (!emails.includes(addr)) emails.push(addr)
  }
  return { emails, invalid, tooMany: emails.length > NOTIFY_EMAILS_MAX }
}

export function formatEmails(emails: string[] | null | undefined): string {
  return (emails || []).join("\n")
}

/** Сообщение об ошибке для формы или null, если ввод валиден. */
export function emailsError(parsed: ParsedEmails): string | null {
  if (parsed.invalid.length) return `Некорректный email: ${parsed.invalid.slice(0, 3).join(", ")}`
  if (parsed.tooMany) return `Не больше ${NOTIFY_EMAILS_MAX} адресов`
  return null
}
