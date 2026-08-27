"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"

interface Notification {
  id: string
  kind: string
  title: string
  body: string | null
  link: string | null
  read_at: string | null
  created_at: string
}

interface NotificationsResponse {
  items: Notification[]
  unread: number
}

export function NotificationsBell() {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const { data } = useSWR<NotificationsResponse>("/notifications", fetcher, {
    refreshInterval: 30000,
  })

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onClick)
    return () => document.removeEventListener("mousedown", onClick)
  }, [open])

  const unread = data?.unread ?? 0

  const openItem = async (n: Notification) => {
    if (!n.read_at) {
      await api(`/notifications/${n.id}/read`, { method: "POST" })
      await mutate("/notifications")
    }
    setOpen(false)
    if (n.link) router.push(n.link)
  }

  const readAll = async () => {
    await api("/notifications/read-all", { method: "POST" })
    await mutate("/notifications")
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={unread > 0 ? `Уведомления: ${unread} непрочитанных` : "Уведомления"}
        className="relative flex h-9 w-9 items-center justify-center rounded-full border border-border bg-panel text-muted shadow-sm transition-colors hover:text-foreground"
      >
        <svg viewBox="0 0 24 24" className="h-4.5 w-4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 01-3.4 0" />
        </svg>
        {unread > 0 ? (
          <span className="absolute -right-1 -top-1 flex h-4.5 min-w-4.5 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="absolute right-0 top-11 z-50 w-80 overflow-hidden rounded-lg border border-border bg-panel shadow-lg">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-xs font-semibold text-foreground">Уведомления</span>
            {unread > 0 ? (
              <button type="button" onClick={readAll} className="text-xs text-primary hover:underline">
                Прочитать все
              </button>
            ) : null}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {data && data.items.length > 0 ? (
              data.items.map((n) => (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => openItem(n)}
                  className={`block w-full border-b border-border px-3 py-2.5 text-left transition-colors last:border-b-0 hover:bg-surface ${
                    n.read_at ? "opacity-60" : ""
                  }`}
                >
                  <span className="flex items-start gap-2">
                    {!n.read_at ? <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden="true" /> : <span className="mt-1.5 h-1.5 w-1.5 shrink-0" aria-hidden="true" />}
                    <span>
                      <span className="block text-xs font-medium text-foreground">{n.title}</span>
                      {n.body ? <span className="mt-0.5 block text-xs leading-relaxed text-muted">{n.body}</span> : null}
                      <span className="mt-0.5 block text-[10px] text-muted">
                        {new Date(n.created_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </span>
                  </span>
                </button>
              ))
            ) : (
              <p className="px-3 py-6 text-center text-xs text-muted">Нет уведомлений</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}
