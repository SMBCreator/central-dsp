/**
 * Справочник «домен → тематика IAB» (кабинет админа): чистые функции без DOM.
 * Серверная валидация (backend/domain_categories.py) главная; здесь только
 * подсказки до отправки и подписи.
 */

export interface DomainCategory {
  domain: string
  iab_codes: string[]
  source: "manual" | "learned"
  hits: number
  updated_at: string | null
}

export interface DomainCategoryPage {
  total: number
  items: DomainCategory[]
}

export interface ImportRowError {
  line: number
  domain: string
  error: string
}

export interface ImportPreview {
  valid: number
  errors: ImportRowError[]
  applied: boolean
  preview?: Array<{ domain: string; iab_codes: string[] }>
  created?: number
  updated?: number
}

export const MAX_MANUAL_CODES = 10
export const PAGE_SIZE = 50

const IAB_CODE_RE = /^IAB\d{1,3}(?:-\d{1,3})?$/

/** Поле ввода кодов: запятые, пробелы и переводы строк равнозначны. */
export function parseCodesInput(text: string): { codes: string[]; invalid: string[] } {
  const codes: string[] = []
  const invalid: string[] = []
  for (const raw of text.split(/[\s,;]+/)) {
    const part = raw.trim().toUpperCase()
    if (!part) continue
    if (!IAB_CODE_RE.test(part)) {
      if (!invalid.includes(part)) invalid.push(part)
    } else if (!codes.includes(part)) {
      codes.push(part)
    }
  }
  return { codes, invalid }
}

/** Сообщение об ошибке поля кодов или null, если можно сохранять. */
export function codesError(text: string): string | null {
  const { codes, invalid } = parseCodesInput(text)
  if (invalid.length) return `Недопустимые коды: ${invalid.join(", ")}. Формат — IAB1 или IAB1-2`
  if (codes.length === 0) return "Укажите хотя бы один код IAB"
  if (codes.length > MAX_MANUAL_CODES) return `Не больше ${MAX_MANUAL_CODES} кодов на домен`
  return null
}

export function sourceLabel(source: string): string {
  return source === "manual" ? "Вручную" : "Из трафика"
}

/** Итог импорта для тоста и сводки под предпросмотром. */
export function importSummary(res: Pick<ImportPreview, "valid" | "errors" | "applied" | "created" | "updated">): string {
  if (res.applied) {
    return `Импортировано: ${res.created ?? 0} новых, ${res.updated ?? 0} обновлено`
  }
  if (res.errors.length === 0) {
    return res.valid > 0 ? `Ошибок нет, готово к применению: ${res.valid}` : "В файле нет строк для импорта"
  }
  return `Корректных строк: ${res.valid}, с ошибками: ${res.errors.length}. Исправьте файл — импорт применяется целиком`
}

/** Можно ли применять импорт: есть строки и нет ошибок. */
export function canApplyImport(res: Pick<ImportPreview, "valid" | "errors"> | null): boolean {
  return !!res && res.valid > 0 && res.errors.length === 0
}
