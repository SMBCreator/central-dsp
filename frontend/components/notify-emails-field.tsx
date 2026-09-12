"use client"

import { Field, inputCls } from "@/components/ui"
import { emailsError, parseEmails } from "@/lib/notify-emails"

/**
 * Поле «Email для уведомлений» (C4b / N-04). Общее для рекламодателя и
 * кампании: textarea со списком адресов, валидация как на сервере, подсказка,
 * кто получит письма при пустом списке.
 */
export function NotifyEmailsField({
  value,
  onChange,
  fallbackHint,
  label = "Email для уведомлений",
}: {
  value: string
  onChange: (text: string) => void
  /** Кто получает письма, если список пуст. */
  fallbackHint: string
  label?: string
}) {
  const parsed = parseEmails(value)
  const error = emailsError(parsed)
  return (
    <Field
      label={label}
      hint={`Письма о бюджете, остановке, датах, отсутствии показов и автоправилах. Несколько адресов — через запятую или с новой строки. ${fallbackHint}`}
    >
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={inputCls + " min-h-[64px] font-mono text-xs"}
        placeholder={"marketing@example.com\nfinance@example.com"}
        rows={2}
        aria-invalid={!!error}
      />
      <span className={"mt-1 block text-xs " + (error ? "text-danger" : "text-muted")}>
        {error
          ? error
          : parsed.emails.length
            ? `${parsed.emails.length} адрес(ов)`
            : fallbackHint}
      </span>
    </Field>
  )
}
