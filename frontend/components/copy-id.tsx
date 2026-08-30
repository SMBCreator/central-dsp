"use client"

// H8: клик по ID (display_id, erid, uuid) копирует значение в буфер с тостом «Скопировано»

import { toastSuccess } from "@/components/toaster"

export function CopyId({
  value,
  label,
  className = "",
}: {
  /** Что копировать */
  value: string | number
  /** Что показывать (по умолчанию — само значение) */
  label?: string
  className?: string
}) {
  const text = String(value)
  const shown = label ?? text

  const copy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    try {
      await navigator.clipboard.writeText(text)
      toastSuccess("Скопировано")
    } catch {
      // Safari без разрешения / http: fallback через выделение
      const el = document.createElement("textarea")
      el.value = text
      document.body.appendChild(el)
      el.select()
      document.execCommand("copy")
      document.body.removeChild(el)
      toastSuccess("Скопировано")
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      title={`Скопировать: ${text}`}
      className={`inline-flex cursor-pointer items-center gap-1 rounded px-1 py-0.5 font-mono text-xs text-muted transition-colors hover:bg-surface hover:text-foreground ${className}`}
    >
      {shown}
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="opacity-50">
        <rect x="9" y="9" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
        <path d="M5 15V5a2 2 0 012-2h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <span className="sr-only">Скопировать идентификатор</span>
    </button>
  )
}
