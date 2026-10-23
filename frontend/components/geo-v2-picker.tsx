"use client"

import { useEffect, useMemo, useState } from "react"
import useSWR from "swr"
import { Field, inputCls } from "@/components/ui"
import { api, fetcher } from "@/lib/api"
import type { Targeting } from "@/lib/types"

export type GeoConfig = { v2_enabled: boolean; dataset_version: string | null; max_places: number }

type Place = {
  id: number
  type: "country" | "region" | "city" | "town"
  name_ru: string
  name_en: string
  country_iso: string
  path: string
  population: number
}

type ResolveRow = { input: string; status: "found" | "ambiguous" | "not_found"; candidates: Place[] }

const TYPE_LABEL: Record<Place["type"], string> = {
  country: "Страна",
  region: "Регион",
  city: "Город",
  town: "Нас. пункт",
}

function placeLabel(p: Place): string {
  return p.name_ru || p.name_en
}

function useDebounced(value: string, ms: number): string {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

function withId(arr: number[] | undefined, id: number): number[] {
  const a = arr || []
  return a.includes(id) ? a : [...a, id]
}

function withoutId(arr: number[] | undefined, id: number): number[] | undefined {
  const a = (arr || []).filter((x) => x !== id)
  return a.length ? a : undefined
}

export function geoV2Summary(t: Targeting): string {
  const parts: string[] = []
  if (t.geo_place_ids?.length) parts.push(`мест: ${t.geo_place_ids.length}`)
  if (t.geo_exclude_place_ids?.length) parts.push(`исключено: ${t.geo_exclude_place_ids.length}`)
  return parts.join(" · ")
}

function PlaceChip({ place, id, excluded, onRemove }: { place?: Place; id: number; excluded?: boolean; onRemove: () => void }) {
  const name = place ? placeLabel(place) : `#${id}`
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm font-medium ${
        excluded ? "bg-danger/10 text-danger" : "bg-panel text-foreground"
      }`}
    >
      <span className="truncate" title={place?.path || undefined}>
        {excluded ? "− " : ""}
        {name}
      </span>
      {place ? <span className="font-mono text-[10px] uppercase tracking-wide opacity-70">{TYPE_LABEL[place.type]}</span> : null}
      <button
        type="button"
        onClick={onRemove}
        className="rounded text-muted transition-colors hover:text-danger"
        aria-label={`Убрать ${name}`}
      >
        ×
      </button>
    </span>
  )
}

function PlaceSearch({ onInclude, onExclude, disabled }: { onInclude: (p: Place) => void; onExclude: (p: Place) => void; disabled: boolean }) {
  const [q, setQ] = useState("")
  const [open, setOpen] = useState(false)
  const query = useDebounced(q.trim(), 200)
  const { data, isLoading } = useSWR<{ items: Place[]; has_more: boolean }>(
    query.length >= 2 ? `/geo/places/search?q=${encodeURIComponent(query)}&limit=15` : null,
    fetcher,
    { keepPreviousData: true },
  )
  const items = query.length >= 2 ? data?.items || [] : []

  const pick = (fn: (p: Place) => void, p: Place) => {
    fn(p)
    setQ("")
    setOpen(false)
  }

  return (
    <div className="relative">
      <input
        className={inputCls}
        value={q}
        disabled={disabled}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={disabled ? "Достигнут лимит мест" : "Страна, регион или город: Краснодар, Подмосковье, Almaty…"}
        aria-label="Поиск места по справочнику"
        role="combobox"
        aria-expanded={open && items.length > 0}
        aria-controls="geo-v2-results"
      />
      {open && query.length >= 2 ? (
        <ul
          id="geo-v2-results"
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-border bg-card py-1 shadow-lg"
        >
          {items.length === 0 ? (
            <li className="px-3 py-2 text-sm text-muted">{isLoading ? "Ищем…" : "Ничего не найдено"}</li>
          ) : (
            items.map((p) => (
              <li key={p.id} role="option" aria-selected={false} className="flex items-center gap-2 px-3 py-1.5 hover:bg-surface">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-foreground">{placeLabel(p)}</div>
                  <div className="truncate text-xs text-muted">
                    {TYPE_LABEL[p.type]} · {p.path || p.country_iso}
                  </div>
                </div>
                <button
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault()
                    pick(onInclude, p)
                  }}
                  className="shrink-0 rounded-md bg-primary px-2 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                >
                  Включить
                </button>
                <button
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault()
                    pick(onExclude, p)
                  }}
                  className="shrink-0 rounded-md border border-border px-2 py-1 text-xs font-medium text-muted hover:border-danger hover:text-danger"
                >
                  Исключить
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  )
}

function BulkAdd({ onAdd }: { onAdd: (places: Place[]) => void }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState("")
  const [country, setCountry] = useState("RU")
  const [rows, setRows] = useState<ResolveRow[] | null>(null)
  const [choice, setChoice] = useState<Record<number, number>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const check = async () => {
    const lines = text.split(/\r?\n|;/).map((s) => s.trim()).filter(Boolean).slice(0, 500)
    if (!lines.length) return
    setBusy(true)
    setErr(null)
    try {
      const res = await api<ResolveRow[]>("/geo/places/resolve", { method: "POST", body: { lines, country } })
      setRows(res)
      setChoice({})
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось проверить список")
    } finally {
      setBusy(false)
    }
  }

  const apply = () => {
    if (!rows) return
    const picked = rows
      .map((r, i) => (r.candidates.length ? r.candidates[choice[i] ?? 0] : undefined))
      .filter((p): p is Place => !!p)
    onAdd(picked)
    setRows(null)
    setText("")
    setOpen(false)
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="text-xs font-medium text-primary hover:underline">
        Добавить списком
      </button>
    )
  }

  const notFound = rows?.filter((r) => r.status === "not_found").length || 0
  return (
    <div className="space-y-2 rounded-lg border border-border bg-surface p-3">
      <div className="flex items-center gap-2">
        <label htmlFor="geo-bulk-country" className="text-xs text-muted">
          Страна
        </label>
        <input
          id="geo-bulk-country"
          value={country}
          maxLength={2}
          onChange={(e) => setCountry(e.target.value.toUpperCase())}
          className={`${inputCls} w-16 font-mono uppercase`}
        />
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={5}
        className={`${inputCls} font-mono text-xs`}
        placeholder={"По одному на строку:\nКраснодар\nСочи\nРеспублика Татарстан"}
        aria-label="Список мест, по одному на строку"
      />
      {rows ? (
        <ul className="max-h-56 space-y-1 overflow-y-auto text-sm">
          {rows.map((r, i) => (
            <li key={`${r.input}-${i}`} className="flex items-center gap-2">
              <span className="w-40 shrink-0 truncate text-muted" title={r.input}>
                {r.input}
              </span>
              {r.status === "not_found" ? (
                <span className="text-danger">не найдено</span>
              ) : r.status === "found" ? (
                <span className="truncate text-foreground">
                  {placeLabel(r.candidates[0])} <span className="text-xs text-muted">{TYPE_LABEL[r.candidates[0].type]}</span>
                </span>
              ) : (
                <select
                  className={`${inputCls} py-1 text-xs`}
                  value={choice[i] ?? 0}
                  onChange={(e) => setChoice({ ...choice, [i]: Number(e.target.value) })}
                  aria-label={`Вариант для «${r.input}»`}
                >
                  {r.candidates.map((c, j) => (
                    <option key={c.id} value={j}>
                      {placeLabel(c)} — {TYPE_LABEL[c.type]}
                      {c.path ? ` (${c.path})` : ""}
                    </option>
                  ))}
                </select>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {err ? <p className="text-xs text-danger">{err}</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        {rows ? (
          <button type="button" onClick={apply} className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90">
            Добавить найденные ({rows.length - notFound})
          </button>
        ) : (
          <button
            type="button"
            onClick={check}
            disabled={busy || !text.trim()}
            className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {busy ? "Проверяем…" : "Проверить"}
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            setOpen(false)
            setRows(null)
          }}
          className="rounded-md px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground"
        >
          Отмена
        </button>
      </div>
    </div>
  )
}

/** GEO v2: выбор мест справочника geo_places (включить/исключить) и режим
 *  для запросов с неопределённым гео. Значения — id мест, как в targeting JSONB. */
export function GeoV2Picker({ value, onChange, config }: { value: Targeting; onChange: (patch: Partial<Targeting>) => void; config: GeoConfig }) {
  const include = value.geo_place_ids || []
  const exclude = value.geo_exclude_place_ids || []
  const total = include.length + exclude.length
  const [known, setKnown] = useState<Record<number, Place>>({})

  const missing = useMemo(() => [...include, ...exclude].filter((id) => !known[id]), [include, exclude, known])
  const { data: fetched } = useSWR<Place[]>(missing.length ? `/geo/places?ids=${missing.join(",")}` : null, fetcher)
  const byId = useMemo(() => {
    const m = { ...known }
    for (const p of fetched || []) m[p.id] = p
    return m
  }, [known, fetched])

  const remember = (ps: Place[]) => setKnown((k) => Object.fromEntries([...Object.entries(k), ...ps.map((p) => [p.id, p])]))

  const includePlace = (p: Place) => {
    if (include.includes(p.id)) return
    remember([p])
    onChange({ geo_place_ids: withId(value.geo_place_ids, p.id), geo_exclude_place_ids: withoutId(value.geo_exclude_place_ids, p.id) })
  }
  const excludePlace = (p: Place) => {
    if (exclude.includes(p.id)) return
    remember([p])
    onChange({ geo_exclude_place_ids: withId(value.geo_exclude_place_ids, p.id), geo_place_ids: withoutId(value.geo_place_ids, p.id) })
  }
  const addMany = (ps: Place[]) => {
    remember(ps)
    const room = Math.max(0, config.max_places - total)
    const next = [...include]
    for (const p of ps) {
      if (next.length - include.length >= room) break
      if (!next.includes(p.id) && !exclude.includes(p.id)) next.push(p.id)
    }
    onChange({ geo_place_ids: next.length ? next : undefined })
  }

  const full = total >= config.max_places
  const lenient = value.geo_unknown === "lenient"

  return (
    <div className="space-y-3">
      <Field
        label="Места показа"
        hint="Регион включает свои города; исключение важнее включения. Пусто — без ограничения по справочнику"
      >
        <PlaceSearch onInclude={includePlace} onExclude={excludePlace} disabled={full} />
      </Field>
      <div className="flex items-center justify-between gap-2">
        <BulkAdd onAdd={addMany} />
        <span className={`font-mono text-xs ${full ? "text-danger" : "text-muted"}`}>
          {total} / {config.max_places}
        </span>
      </div>
      {include.length || exclude.length ? (
        <div className="flex flex-wrap gap-1.5" aria-label="Выбранные места">
          {include.map((id) => (
            <PlaceChip key={`i-${id}`} id={id} place={byId[id]} onRemove={() => onChange({ geo_place_ids: withoutId(value.geo_place_ids, id) })} />
          ))}
          {exclude.map((id) => (
            <PlaceChip
              key={`e-${id}`}
              id={id}
              place={byId[id]}
              excluded
              onRemove={() => onChange({ geo_exclude_place_ids: withoutId(value.geo_exclude_place_ids, id) })}
            />
          ))}
        </div>
      ) : null}
      {total ? (
        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-sm font-medium text-foreground">Если SSP не передала гео или оно не распознано</legend>
          <label className="flex items-start gap-2 text-sm text-foreground">
            <input
              type="radio"
              name="geo_unknown"
              checked={!lenient}
              onChange={() => onChange({ geo_unknown: undefined })}
              className="mt-0.5"
            />
            <span>
              Не показывать <span className="text-muted">— точнее, но меньше охват</span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm text-foreground">
            <input
              type="radio"
              name="geo_unknown"
              checked={lenient}
              onChange={() => onChange({ geo_unknown: "lenient" })}
              className="mt-0.5"
            />
            <span>
              Показывать{" "}
              <span className="text-muted">
                — и когда известна только страна или регион, содержащие выбранное место
              </span>
            </span>
          </label>
        </fieldset>
      ) : null}
    </div>
  )
}
