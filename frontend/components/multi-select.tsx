"use client"

import { useEffect, useMemo, useRef, useState } from "react"

export type MultiSelectOption = {
  value: string
  label: string
  hint?: string
}

/**
 * C1: универсальный мультиселект для таргетингов.
 * Кнопка-триггер показывает первые 2–3 значения или «Выбрано: N»,
 * в дропдауне — поиск + чекбоксы + «Выбрать все» / «Сбросить».
 * Выбранные значения — компактные чипы с крестиками под полем.
 */
export function MultiSelect({
  options,
  selected,
  onChange,
  placeholder = "Не выбрано (все)",
  searchable = true,
  showChips = true,
}: {
  options: MultiSelectOption[]
  selected: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  searchable?: boolean
  showChips?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  const byValue = useMemo(() => new Map(options.map((o) => [o.value, o])), [options])

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase()
    if (!query) return options
    return options.filter(
      (o) => o.label.toLowerCase().includes(query) || o.value.toLowerCase().includes(query),
    )
  }, [options, q])

  const triggerText = useMemo(() => {
    if (selected.length === 0) return placeholder
    if (selected.length <= 3) {
      return selected.map((v) => byValue.get(v)?.label ?? v).join(", ")
    }
    return `Выбрано: ${selected.length}`
  }, [selected, byValue, placeholder])

  const toggleValue = (v: string) => {
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v])
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className={`flex h-9 w-full items-center justify-between gap-2 rounded-md border border-border bg-card px-3 text-left text-sm transition-colors hover:border-muted ${
          selected.length === 0 ? "text-muted" : "text-foreground"
        }`}
      >
        <span className="truncate">{triggerText}</span>
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
          className={`shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
        >
          <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open ? (
        <div className="absolute z-30 mt-1 w-full min-w-56 rounded-md border border-border bg-card shadow-lg">
          {searchable && options.length > 6 ? (
            <div className="border-b border-border p-2">
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Поиск…"
                className="h-8 w-full rounded border border-border bg-surface px-2 text-sm text-foreground outline-none focus:border-accent"
                aria-label="Поиск по вариантам"
              />
            </div>
          ) : null}
          <div className="flex items-center gap-3 border-b border-border px-3 py-1.5">
            <button
              type="button"
              onClick={() => onChange(options.map((o) => o.value))}
              className="text-xs font-medium text-accent hover:underline"
            >
              Выбрать все
            </button>
            <button
              type="button"
              onClick={() => onChange([])}
              className="text-xs font-medium text-muted hover:text-foreground hover:underline"
            >
              Сбросить
            </button>
            <span className="ml-auto font-mono text-[10px] text-muted">
              {selected.length}/{options.length}
            </span>
          </div>
          <ul role="listbox" aria-multiselectable="true" className="max-h-56 overflow-y-auto py-1">
            {filtered.map((o) => {
              const checked = selected.includes(o.value)
              return (
                <li key={o.value} role="option" aria-selected={checked}>
                  <label className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 text-sm text-foreground hover:bg-surface">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleValue(o.value)}
                      className="h-3.5 w-3.5 accent-current"
                    />
                    <span className="truncate">{o.label}</span>
                    {o.hint ? <span className="ml-auto shrink-0 font-mono text-[10px] text-muted">{o.hint}</span> : null}
                  </label>
                </li>
              )
            })}
            {filtered.length === 0 ? (
              <li className="px-3 py-2 text-xs text-muted">Ничего не найдено</li>
            ) : null}
          </ul>
        </div>
      ) : null}

      {showChips && selected.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {selected.map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => toggleValue(v)}
              className="inline-flex items-center gap-1 rounded-full bg-panel px-2.5 py-0.5 text-xs font-medium text-foreground transition-colors hover:bg-border"
              title="Убрать"
            >
              {byValue.get(v)?.label ?? v} <span aria-hidden>×</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
