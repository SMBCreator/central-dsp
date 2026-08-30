"use client"

// F5/H8: лёгкая toast-система без зависимостей.
// toast() можно вызывать из любого места (включая lib/api.ts) — компонент <Toaster />
// смонтирован один раз в app-shell и слушает события.

import { useEffect, useState } from "react"

export type ToastKind = "error" | "success" | "info"

type ToastItem = {
  id: number
  kind: ToastKind
  message: string
}

type Listener = (t: ToastItem) => void

let nextId = 1
const listeners = new Set<Listener>()
// Дедупликация: одинаковые сообщения в течение 3 секунд не дублируются
const recent = new Map<string, number>()

export function toast(message: string, kind: ToastKind = "info") {
  const key = `${kind}:${message}`
  const now = Date.now()
  const last = recent.get(key)
  if (last && now - last < 3000) return
  recent.set(key, now)
  const item: ToastItem = { id: nextId++, kind, message }
  for (const fn of listeners) fn(item)
}

export function toastError(message: string) {
  toast(message, "error")
}

export function toastSuccess(message: string) {
  toast(message, "success")
}

const KIND_STYLES: Record<ToastKind, string> = {
  error: "border-danger/40 bg-danger/10 text-danger",
  success: "border-success/40 bg-success/10 text-success",
  info: "border-border bg-panel text-foreground",
}

export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([])

  useEffect(() => {
    const onToast: Listener = (t) => {
      setItems((prev) => [...prev.slice(-4), t])
      window.setTimeout(() => {
        setItems((prev) => prev.filter((x) => x.id !== t.id))
      }, 5000)
    }
    listeners.add(onToast)
    return () => {
      listeners.delete(onToast)
    }
  }, [])

  if (items.length === 0) return null

  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-80 flex-col gap-2"
    >
      {items.map((t) => (
        <div
          key={t.id}
          role={t.kind === "error" ? "alert" : "status"}
          className={`pointer-events-auto flex items-start gap-2 rounded-md border px-3 py-2.5 text-xs shadow-lg backdrop-blur ${KIND_STYLES[t.kind]}`}
        >
          <span className="min-w-0 flex-1 break-words leading-relaxed">{t.message}</span>
          <button
            type="button"
            onClick={() => setItems((prev) => prev.filter((x) => x.id !== t.id))}
            className="shrink-0 opacity-60 transition-opacity hover:opacity-100"
            aria-label="Закрыть уведомление"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  )
}
