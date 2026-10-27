"use client"

// H2: единая модалка подтверждения опасных действий вместо window.confirm().
// Используется для удаления групп, пикселей, сегментов и архивирования кампаний.

import { useEffect, useRef } from "react"
import { Button } from "@/components/ui"

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Удалить",
  variant = "danger",
  busy = false,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  description: string
  confirmLabel?: string
  variant?: "danger" | "default"
  busy?: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  const confirmRef = useRef<HTMLButtonElement>(null)
  // Инлайн-onClose у родителя меняется каждый рендер; в зависимостях эффекта он
  // перезапускал бы эффект и заново ставил фокус на «Подтвердить» (ТЗ п.4, sweep).
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    if (!open) return
    confirmRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-5 shadow-lg">
        <h2 id="confirm-dialog-title" className="text-sm font-semibold text-foreground">
          {title}
        </h2>
        <p className="mt-2 text-xs leading-relaxed text-muted">{description}</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <button
            ref={confirmRef}
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 ${
              variant === "danger"
                ? "bg-danger text-white hover:bg-danger/90"
                : "bg-accent text-accent-foreground hover:bg-accent/90"
            }`}
          >
            {busy ? "Выполняется…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
