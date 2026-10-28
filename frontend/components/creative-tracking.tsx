"use client"

import { useId, useState } from "react"
import { api, describeApiError } from "@/lib/api"
import { Button } from "@/components/ui"
import { PixelFieldsBlock } from "@/components/pixel-fields"
import { useMacroCatalog } from "@/components/macro-picker"
import { insecurePixelWarning, unknownMacros, type MacroEntry } from "@/lib/url-macros"
import { isHttpUrl } from "@/lib/creative-batch"

export interface TrackingFields {
  click_url?: string | null
  imp_tracker_url?: string | null
  click_tracker_url?: string | null
}

const TOKEN_SPLIT = /(\$\{CDSP_[^}]*\})/gi

function truncate(s: string, max = 90): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}

/** Адрес с подсветкой макросов: известные — акцентом, неизвестные — предупреждением. */
export function MacroText({ value, catalog }: { value: string; catalog: MacroEntry[] }) {
  const keys = new Set(catalog.map((e) => e.key))
  return (
    <>
      {value.split(TOKEN_SPLIT).map((part, i) => {
        if (!/^\$\{CDSP_[^}]*\}$/i.test(part)) return <span key={i}>{part}</span>
        const known = part.startsWith("${CDSP_") && keys.has(part.slice("${CDSP_".length, -1))
        return (
          <mark
            key={i}
            className={`rounded px-0.5 font-medium ${known ? "bg-primary/10 text-primary" : "bg-warning/15 text-warning"}`}
            title={known ? undefined : "Неизвестный макрос"}
          >
            {part}
          </mark>
        )
      })}
    </>
  )
}

/** Значки «показ» / «клик» для таблицы: серые, если не заданы; адрес — в подсказке при наведении и фокусе. */
export function PixelBadges({ imp, click }: { imp?: string | null; click?: string | null }) {
  return (
    <div className="flex items-center gap-1.5">
      <PixelBadge label="показ" url={imp} />
      <PixelBadge label="клик" url={click} />
    </div>
  )
}

function PixelBadge({ label, url }: { label: string; url?: string | null }) {
  const tipId = useId()
  const set = !!url
  return (
    <span className="group relative inline-flex">
      <span
        tabIndex={0}
        aria-describedby={set ? tipId : undefined}
        aria-label={set ? `Пиксель ${label}: задан` : `Пиксель ${label}: не задан`}
        className={`inline-flex cursor-default items-center rounded px-1.5 py-0.5 text-[11px] font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
          set ? "bg-primary/10 text-primary" : "bg-panel text-muted/70"
        }`}
      >
        {label}
      </span>
      {set ? (
        <span
          id={tipId}
          role="tooltip"
          className="pointer-events-none invisible absolute left-0 top-full z-20 mt-1 w-64 break-all rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[11px] text-foreground opacity-0 shadow-md group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
        >
          {truncate(url!, 160)}
        </span>
      ) : null}
    </span>
  )
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      aria-label={`Скопировать: ${label}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        } catch {
          setCopied(false)
        }
      }}
      className="shrink-0 rounded-md border border-border px-2 py-0.5 text-[11px] font-medium text-muted transition-colors hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
    >
      {copied ? "Скопировано" : "Копировать"}
    </button>
  )
}

function TrackingRow({ label, value, catalog, link }: { label: string; value?: string | null; catalog: MacroEntry[]; link?: boolean }) {
  const unknown = value ? unknownMacros(value, catalog) : []
  const insecure = value && !link ? insecurePixelWarning(value) : null
  const httpLink = value && link && /^http:\/\//i.test(value.trim())
  return (
    <div className="space-y-1">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium text-muted">{label}</p>
          {value ? (
            <p className="mt-0.5 break-all font-mono text-xs text-foreground">
              <MacroText value={value} catalog={catalog} />
            </p>
          ) : (
            <p className="mt-0.5 text-xs text-muted">не задан</p>
          )}
        </div>
        {value ? <CopyButton value={value} label={label} /> : null}
      </div>
      {unknown.length > 0 ? (
        <p role="alert" className="text-[11px] text-warning">
          Неизвестные макросы: {unknown.map((u) => (u.suggestion ? `${u.token} (возможно, \${CDSP_${u.suggestion}})` : u.token)).join(", ")}. В показ они не подставятся.
        </p>
      ) : null}
      {insecure ? <p className="text-[11px] text-warning">{insecure}</p> : null}
      {httpLink ? <p className="text-[11px] text-warning">Ссылка перехода по http://: браузер может показать предупреждение.</p> : null}
    </div>
  )
}

/** Блок «Трекинг»: ссылка перехода и пиксели (только чтение). */
export function TrackingBlock({ creative }: { creative: TrackingFields }) {
  const catalog = useMacroCatalog()
  const hasPixels = !!(creative.imp_tracker_url || creative.click_tracker_url)
  return (
    <section aria-label="Трекинг" className="space-y-3 rounded-md border border-border bg-surface p-3">
      <h3 className="text-xs font-semibold text-foreground">Трекинг</h3>
      <TrackingRow label="Ссылка перехода" value={creative.click_url} catalog={catalog} link />
      <TrackingRow label="Пиксель показа" value={creative.imp_tracker_url} catalog={catalog} />
      <TrackingRow label="Пиксель клика" value={creative.click_tracker_url} catalog={catalog} />
      {!hasPixels ? <p className="text-[11px] text-muted">Пиксели не заданы.</p> : null}
    </section>
  )
}

export interface PixelEditTarget {
  id: string
  status: string
  imp_tracker_url?: string | null
  click_tracker_url?: string | null
}

/** Кнопка «Изменить пиксели» и форма правки (две ссылки + панель макросов). */
export function PixelEditor({
  creative,
  admin,
  onSaved,
}: {
  creative: PixelEditTarget
  admin: boolean
  onSaved: (updated: PixelEditTarget) => void
}) {
  const [editing, setEditing] = useState(false)
  const [imp, setImp] = useState("")
  const [click, setClick] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const start = () => {
    setImp(creative.imp_tracker_url || "")
    setClick(creative.click_tracker_url || "")
    setError(null)
    setEditing(true)
  }

  const invalid = (v: string) => v.trim() !== "" && !isHttpUrl(v)
  const unchanged = imp.trim() === (creative.imp_tracker_url || "") && click.trim() === (creative.click_tracker_url || "")
  const needsRemoderation = !admin && creative.status === "approved"

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      // Пустая строка очищает поле на сервере.
      const updated = await api<PixelEditTarget>(`/creatives/${creative.id}`, {
        method: "PATCH",
        body: { imp_tracker_url: imp.trim(), click_tracker_url: click.trim() },
      })
      onSaved(updated)
      setEditing(false)
    } catch (err) {
      setError(describeApiError(err, "Не удалось сохранить пиксели"))
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <Button variant="secondary" onClick={start}>
        Изменить пиксели
      </Button>
    )
  }

  return (
    <div className="space-y-3 rounded-md border border-border bg-surface p-3">
      <PixelFieldsBlock idPrefix={`edit-${creative.id}`} title="Пиксели креатива" imp={imp} click={click} onImp={setImp} onClick={setClick} defaultOpen />
      <p className="text-[11px] text-muted">
        Изменение дойдёт до показов с очередным обновлением фида (обычно до 30 секунд). Пустое поле удаляет пиксель.
      </p>
      {needsRemoderation ? (
        <p className="text-[11px] text-warning">
          Креатив одобрен: после сохранения он вернётся на повторную модерацию и до одобрения не будет участвовать в показах.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-[11px] text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => setEditing(false)} disabled={saving}>
          Отмена
        </Button>
        <Button onClick={save} disabled={saving || unchanged || invalid(imp) || invalid(click)}>
          {saving ? "Сохранение…" : "Сохранить"}
        </Button>
      </div>
    </div>
  )
}
