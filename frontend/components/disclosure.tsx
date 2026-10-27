"use client"

import { useId, useState, type ReactNode } from "react"

/** Сворачиваемый блок. Содержимое НЕ размонтируется (атрибут hidden): поля с
 *  defaultValue + onBlur не должны терять набранный текст при сворачивании. */
export function Disclosure({
  title,
  summary,
  accent = false,
  defaultOpen = false,
  actions,
  titleClassName = "text-base font-bold text-foreground",
  children,
}: {
  title: string
  summary: string
  /** Есть ненулевые настройки: сводка подсвечивается, чтобы скрытые ограничения были заметны. */
  accent?: boolean
  defaultOpen?: boolean
  /** Кнопки справа от заголовка (например, «Сбросить»). Вне кнопки-заголовка: кнопки не вкладываются. */
  actions?: ReactNode
  titleClassName?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  const bodyId = useId()
  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls={bodyId}
          className="-mx-1 flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            className={`shrink-0 text-muted transition-transform ${open ? "rotate-90" : ""}`}
          >
            <path d="M9 6l6 6-6 6" />
          </svg>
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span className={titleClassName}>{title}</span>
            <span className={`min-w-0 break-words text-xs ${accent ? "font-medium text-warning" : "text-muted"}`}>
              {summary}
            </span>
          </span>
        </button>
        {actions}
      </div>
      <div id={bodyId} hidden={!open} className="mt-4 space-y-4">
        {children}
      </div>
    </>
  )
}
