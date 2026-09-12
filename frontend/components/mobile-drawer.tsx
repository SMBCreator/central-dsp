"use client"

import { useRef, type ReactNode } from "react"
import { useFocusTrap } from "@/lib/focus-trap"

/**
 * F-01: мобильное меню как настоящий dialog — Escape закрывает, Tab циклится
 * внутри, фокус при открытии уходит в панель, при закрытии возвращается на бургер.
 */
export function MobileDrawer({
  open,
  onClose,
  label,
  children,
}: {
  open: boolean
  onClose: () => void
  label: string
  children: ReactNode
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  useFocusTrap(panelRef, open, onClose)

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 md:hidden">
      {/* подложка: клик закрывает; для клавиатуры есть кнопка «Закрыть меню» внутри панели */}
      <div aria-hidden="true" onClick={onClose} className="absolute inset-0 bg-foreground/40" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className="relative flex h-full w-60 flex-col shadow-xl outline-none"
      >
        <button
          type="button"
          aria-label="Закрыть меню"
          data-focus-skip
          onClick={onClose}
          className="absolute -right-11 top-3 flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-card text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
        {children}
      </div>
    </div>
  )
}
