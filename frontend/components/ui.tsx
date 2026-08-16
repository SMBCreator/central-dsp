"use client"

import type { ReactNode } from "react"
import { STATUS_LABELS } from "@/lib/types"

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-lg border border-border bg-card ${className}`}>{children}</div>
}

export function CardHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
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
    <Card className="px-5 py-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1.5 font-mono text-2xl font-semibold tracking-tight text-foreground">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
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
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${styles[status] || "bg-muted/10 text-muted"}`}
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
    primary: "bg-primary text-primary-foreground hover:opacity-90",
    secondary: "border border-border bg-card text-foreground hover:bg-surface",
    danger: "bg-danger text-primary-foreground hover:opacity-90",
    ghost: "text-muted hover:bg-surface hover:text-foreground",
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]} ${className}`}
    >
      {children}
    </button>
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
  "w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"

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
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" />
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
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 pt-12" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`w-full ${wide ? "max-w-2xl" : "max-w-md"} rounded-lg border border-border bg-card shadow-xl`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          <button type="button" onClick={onClose} className="text-muted hover:text-foreground" aria-label="Закрыть">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  )
}

export function Table({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
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
