"use client"

import { useEffect, useRef, type ReactNode } from "react"
import Link from "next/link"
import { useFocusTrap } from "@/lib/focus-trap"
import { STATUS_LABELS } from "@/lib/types"

/*
 * UI kit restyled after next-shadcn-admin-dashboard (arhamkhnz):
 * flat bordered cards (rounded-xl + shadow-xs), compact h-8 buttons with
 * active:translate-y-px, focus rings. Component APIs are unchanged.
 * Amicro flavor retained: pill tabs, soft transitions.
 */

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-border bg-card text-foreground shadow-xs ${className}`}>
      {children}
    </div>
  )
}

/** Inner nested panel (card-in-card pattern) */
export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-lg bg-panel ${className}`}>{children}</div>
}

export function CardHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 px-6 pb-2 pt-5">
      <div>
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-xs text-muted">{subtitle}</p> : null}
      </div>
      {action}
    </div>
  )
}

export function KpiCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="px-6 py-5">
      <p className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-muted">{label}</p>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-foreground">{value}</p>
      {hint ? <p className="mt-1 font-mono text-[11px] text-muted">{hint}</p> : null}
    </Card>
  )
}

/**
 * Chart-card anatomy: UPPERCASE mono label + badge, big metric with small
 * unit, chart body in a nested panel, monospace caption row at the bottom.
 */
export function ChartCard({
  label,
  badge,
  metric,
  metricUnit,
  footerLeft,
  footerRight,
  children,
  className = "",
}: {
  label: string
  badge?: string
  metric?: string
  metricUnit?: string
  footerLeft?: string
  footerRight?: string
  children: ReactNode
  className?: string
}) {
  return (
    <Card className={`flex flex-col px-6 py-5 ${className}`}>
      <div className="flex items-center gap-2.5">
        <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">{label}</p>
        {badge ? (
          <span className="rounded-md border border-border bg-panel px-2 py-0.5 font-mono text-[10px] text-foreground">
            {badge}
          </span>
        ) : null}
      </div>
      {metric ? (
        <p className="mt-1.5 text-2xl font-semibold tracking-tight text-foreground">
          {metric}
          {metricUnit ? <span className="ml-1.5 text-xs font-normal text-muted">{metricUnit}</span> : null}
        </p>
      ) : null}
      <div className="mt-3 flex-1 rounded-lg bg-panel/60 p-2">{children}</div>
      {footerLeft || footerRight ? (
        <div className="mt-3 flex items-center justify-between border-t border-border pt-3">
          <span className="font-mono text-[11px] text-muted">{footerLeft}</span>
          <span className="font-mono text-[11px] font-medium text-foreground">{footerRight}</span>
        </div>
      ) : null}
    </Card>
  )
}

export function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    active: "bg-success/10 text-success",
    approved: "bg-success/10 text-success",
    paused: "bg-warning/10 text-warning",
    pending: "bg-warning/10 text-warning",
    testing: "bg-warning/10 text-warning",
    draft: "bg-muted/10 text-muted",
    archived: "bg-muted/10 text-muted",
    completed: "bg-accent/10 text-accent",
    rejected: "bg-danger/10 text-danger",
    // ОРД
    registered: "bg-success/10 text-success",
    sending: "bg-warning/10 text-warning",
    failed: "bg-danger/10 text-danger",
    erir_error: "bg-danger/10 text-danger",
    awaiting_contract: "bg-warning/10 text-warning",
    awaiting_kktu: "bg-warning/10 text-warning",
    superseded: "bg-muted/10 text-muted",
  }
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${styles[status] || "bg-muted/10 text-muted"}`}
    >
      {STATUS_LABELS[status] || status}
    </span>
  )
}

export function Button({
  children,
  onClick,
  variant = "primary",
  type = "button",
  disabled,
  className = "",
  href,
}: {
  children: ReactNode
  onClick?: () => void
  variant?: "primary" | "secondary" | "danger" | "ghost"
  type?: "button" | "submit"
  disabled?: boolean
  className?: string
  /** Кнопка-ссылка: рендерится <a> (next/link) с теми же стилями. Вложение <button> в <a> невалидно (F-01). */
  href?: string
}) {
  const styles = {
    primary: "bg-primary text-primary-foreground hover:bg-primary/85",
    secondary: "border-border bg-card text-foreground hover:bg-panel",
    danger: "bg-danger/10 text-danger hover:bg-danger/20",
    ghost: "text-muted hover:bg-panel hover:text-foreground",
  }
  const cls = `inline-flex h-8 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-transparent px-3 text-sm font-medium outline-none transition-all select-none focus-visible:ring-2 focus-visible:ring-ring/50 active:translate-y-px disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]} ${className}`
  if (href !== undefined) {
    return (
      <Link href={href} onClick={onClick} className={cls} aria-disabled={disabled || undefined}>
        {children}
      </Link>
    )
  }
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={cls}>
      {children}
    </button>
  )
}

/** Pill tab group (Amicro flavor) */
export function PillTabs<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T
  onChange: (v: T) => void
  options: readonly { key: T; label: string }[]
}) {
  return (
    <div className="inline-flex items-center gap-1 rounded-lg bg-panel p-1">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
            value === o.key ? "bg-card text-foreground shadow-xs" : "text-muted hover:text-foreground"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Подсказка под знаком «?» — раскрывается по наведению/фокусу, не занимает место в форме. */
export function HelpTip({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        tabIndex={0}
        aria-label={`Подсказка: ${text}`}
        className="flex h-4 w-4 items-center justify-center rounded-full bg-panel text-[10px] font-semibold leading-none text-muted transition-colors hover:bg-border hover:text-foreground focus:bg-border focus:text-foreground focus:outline-none"
        onClick={(e) => e.preventDefault()}
      >
        ?
      </button>
      <span
        role="tooltip"
        className="pointer-events-none invisible absolute bottom-full left-1/2 z-40 mb-1.5 w-56 -translate-x-1/2 rounded-md border border-border bg-card px-2.5 py-1.5 text-left text-xs font-normal leading-relaxed text-foreground opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        {text}
      </span>
    </span>
  )
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: ReactNode
  hint?: string
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-foreground">
        {label}
        {hint ? <HelpTip text={hint} /> : null}
      </span>
      {children}
    </label>
  )
}

export const inputCls =
  "w-full rounded-lg border border-input bg-card px-3 py-1.5 text-sm text-foreground placeholder:text-muted transition-[border-color,box-shadow] focus:border-ring focus:ring-2 focus:ring-ring/40 focus:outline-none"

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description ? <p className="max-w-sm text-xs text-muted">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}

export function Spinner() {
  return (
    <div className="flex items-center justify-center py-16" role="status" aria-label="Загрузка">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-foreground" />
    </div>
  )
}

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  wide,
  size,
}: {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
  /** Эквивалент size="lg" (обратная совместимость) */
  wide?: boolean
  /** md — 28rem, lg — 42rem, xl — 64rem (двухколоночные редакторы) */
  size?: "md" | "lg" | "xl"
}) {
  const dialogRef = useRef<HTMLDivElement>(null)

  // Esc закрывает; фокус переносится в диалог при открытии и возвращается
  // на кнопку-инициатор при закрытии (ТЗ п.5: модалки — Esc, фокус-ловушка).
  // Реализация общая с мобильным меню — lib/focus-trap.ts (F-01).
  useFocusTrap(dialogRef, open, onClose, {
    initialFocus: () =>
      dialogRef.current?.querySelector<HTMLElement>(
        'input:not([type=hidden]), select, textarea, button:not([aria-label="Закрыть"]), [href], [tabindex]:not([tabindex="-1"])',
      ) ?? null,
  })

  if (!open) return null
  const widthCls = size === "xl" ? "max-w-5xl" : size === "lg" || wide ? "max-w-2xl" : "max-w-md"
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 pt-12"
      onMouseDown={(e) => {
        // Закрываем только по клику именно по подложке (не по отпусканию мыши
        // после выделения текста внутри формы).
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`w-full ${widthCls} rounded-xl border border-border bg-card shadow-lg outline-none`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 px-6 pb-2 pt-5">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">{title}</h2>
            {subtitle ? <p className="mt-0.5 text-xs text-muted">{subtitle}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors hover:bg-panel hover:text-foreground"
            aria-label="Закрыть"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="px-6 pb-6 pt-2">{children}</div>
      </div>
    </div>
  )
}

export function Table({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto px-2 pb-2">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            {headers.map((h) => (
              <th key={h} className="whitespace-nowrap px-4 py-2.5 text-left text-xs font-medium text-muted">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">{children}</tbody>
      </table>
    </div>
  )
}
