"use client"

import type { ReactNode } from "react"
import { STATUS_LABELS } from "@/lib/types"

/* amicro-style: rounded-3xl white cards with soft shadow, pill buttons, nested inner panels */

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-3xl border border-border bg-card shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_-12px_rgba(0,0,0,0.08)] ${className}`}
    >
      {children}
    </div>
  )
}

/** Inner nested panel (amicro card-in-card pattern) */
export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-2xl bg-panel ${className}`}>{children}</div>
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
      <p className="mt-2 text-2xl font-bold tracking-tight text-foreground">{value}</p>
      {hint ? <p className="mt-1 font-mono text-[11px] text-muted">{hint}</p> : null}
    </Card>
  )
}

/**
 * amicro chart-card anatomy: UPPERCASE mono label + pill badge,
 * big metric with small unit, chart body in a nested panel,
 * monospace caption row at the bottom.
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
          <span className="rounded-full border border-border bg-panel px-2.5 py-0.5 font-mono text-[10px] text-foreground">
            {badge}
          </span>
        ) : null}
      </div>
      {metric ? (
        <p className="mt-1.5 text-2xl font-bold tracking-tight text-foreground">
          {metric}
          {metricUnit ? <span className="ml-1.5 text-xs font-normal text-muted">{metricUnit}</span> : null}
        </p>
      ) : null}
      <div className="mt-3 flex-1 rounded-2xl bg-panel/60 p-2">{children}</div>
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
    draft: "bg-muted/10 text-muted",
    archived: "bg-muted/10 text-muted",
    completed: "bg-accent/10 text-accent",
    rejected: "bg-danger/10 text-danger",
  }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${styles[status] || "bg-muted/10 text-muted"}`}
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
}: {
  children: ReactNode
  onClick?: () => void
  variant?: "primary" | "secondary" | "danger" | "ghost"
  type?: "button" | "submit"
  disabled?: boolean
  className?: string
}) {
  const styles = {
    primary: "bg-primary text-primary-foreground hover:bg-primary-hover active:scale-[0.98]",
    secondary: "bg-panel text-foreground hover:bg-surface-hover active:scale-[0.98]",
    danger: "bg-danger text-white hover:opacity-90 active:scale-[0.98]",
    ghost: "text-muted hover:bg-panel hover:text-foreground",
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-1.5 rounded-full px-4 py-2 text-sm font-medium transition-all disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]} ${className}`}
    >
      {children}
    </button>
  )
}

/** amicro pill tab group */
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
    <div className="inline-flex items-center gap-1 rounded-full bg-panel p-1">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
            value === o.key ? "bg-card text-foreground shadow-sm" : "text-muted hover:text-foreground"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
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
      <span className="mb-1.5 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-xs text-muted">{hint}</span> : null}
    </label>
  )
}

export const inputCls =
  "w-full rounded-xl border border-border bg-panel px-3.5 py-2 text-sm text-foreground placeholder:text-muted transition-colors focus:border-foreground/30 focus:bg-card focus:outline-none"

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
  children,
  wide,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  wide?: boolean
}) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 pt-12 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`w-full ${wide ? "max-w-2xl" : "max-w-md"} rounded-3xl border border-border bg-card shadow-2xl`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 pb-2 pt-5">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-panel text-muted transition-colors hover:text-foreground"
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
          <tr>
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
