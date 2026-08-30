"use client"

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { fetcher } from "@/lib/api"

/* ---------- Правая выезжающая панель (как на Авито) ---------- */

export function SideDrawer({
  open,
  onClose,
  title,
  titleBadge,
  footer,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  titleBadge?: string
  footer?: ReactNode
  children: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", onKey)
    document.body.style.overflow = "hidden"
    return () => {
      document.removeEventListener("keydown", onKey)
      document.body.style.overflow = ""
    }
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={title}>
      <button
        type="button"
        aria-label="Закрыть панель"
        onClick={onClose}
        className="absolute inset-0 bg-black/40 backdrop-blur-[1px]"
      />
      <div className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col border-l border-border bg-card shadow-2xl">
        <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-5">
          <h2 className="text-lg font-bold text-foreground">
            {title}
            {titleBadge ? <span className="ml-2 align-middle text-sm font-semibold text-muted">{titleBadge}</span> : null}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="rounded-md p-1 text-muted transition-colors hover:bg-panel hover:text-foreground"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4">{children}</div>
        {footer ? <div className="border-t border-border px-5 py-3">{footer}</div> : null}
      </div>
    </div>
  )
}

/* ---------- Регионы России ---------- */

export const RU_REGIONS: string[] = [
  "Москва", "Санкт-Петербург", "Московская область", "Ленинградская область",
  "Адыгея", "Алтай", "Алтайский край", "Амурская область", "Архангельская область",
  "Астраханская область", "Башкортостан", "Белгородская область", "Брянская область",
  "Бурятия", "Владимирская область", "Волгоградская область", "Вологодская область",
  "Воронежская область", "Дагестан", "Еврейская АО", "Забайкальский край",
  "Ивановская область", "Ингушетия", "Иркутская область", "Кабардино-Балкария",
  "Калининградская область", "Калмыкия", "Калужская область", "Камчатский край",
  "Карачаево-Черкесия", "Карелия", "Кемеровская область", "Кировская область",
  "Коми", "Костромская область", "Краснодарский край", "Красноярский край",
  "Крым", "Курганская область", "Курская область", "Липецкая область",
  "Магаданская область", "Марий Эл", "Мордовия", "Мурманская область",
  "Ненецкий АО", "Нижегородская область", "Новгородская область", "Новосибирская область",
  "Омская область", "Оренбургская область", "Орловская область", "Пензенская область",
  "Пермский край", "Приморский край", "Псковская область", "Ростовская область",
  "Рязанская область", "Самарская область", "Саратовская область", "Саха (Якутия)",
  "Сахалинская область", "Свердловская область", "Севастополь", "Северная Осетия",
  "Смоленская область", "Ставропольский край", "Тамбовская область", "Татарстан",
  "Тверская область", "Томская область", "Тульская область", "Тыва",
  "Тюменская область", "Удмуртия", "Ульяновская область", "Хабаровский край",
  "Хакасия", "Ханты-Мансийский АО", "Челябинская область", "Чечня",
  "Чувашия", "Чукотский АО", "Ямало-Ненецкий АО", "Ярославская область",
]

type GeoCity = { kind: "country" | "region" | "city"; label: string; value: string; hint: string }

/* ---------- Панель «География» (как на Авито) ---------- */

export function GeoDrawer({
  open,
  onClose,
  regions,
  cities,
  onApply,
}: {
  open: boolean
  onClose: () => void
  regions: string[]
  cities: string[]
  onApply: (next: { regions: string[]; cities: string[] }) => void
}) {
  const [selRegions, setSelRegions] = useState<string[]>(regions)
  const [selCities, setSelCities] = useState<string[]>(cities)
  const [q, setQ] = useState("")
  const [cityResults, setCityResults] = useState<GeoCity[]>([])
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // при каждом открытии — синхронизация с текущим значением
  useEffect(() => {
    if (open) {
      setSelRegions(regions)
      setSelCities(cities)
      setQ("")
      setCityResults([])
    }
  }, [open, regions, cities])

  // поиск городов через backend (регионы фильтруются локально)
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    const query = q.trim()
    if (query.length < 2) {
      setCityResults([])
      return
    }
    timer.current = setTimeout(async () => {
      try {
        const res = (await fetcher(`/geo/search?q=${encodeURIComponent(query)}`)) as GeoCity[]
        setCityResults(res.filter((r) => r.kind === "city"))
      } catch {
        setCityResults([])
      }
    }, 200)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [q])

  const query = q.trim().toLowerCase()
  const shownRegions = useMemo(
    () => (query ? RU_REGIONS.filter((r) => r.toLowerCase().includes(query)) : RU_REGIONS),
    [query],
  )
  const allSelected = selRegions.length === 0 && selCities.length === 0
  const total = selRegions.length + selCities.length

  const toggleRegion = (r: string) =>
    setSelRegions((prev) => (prev.includes(r) ? prev.filter((x) => x !== r) : [...prev, r]))
  const toggleCity = (c: string) =>
    setSelCities((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]))

  return (
    <SideDrawer
      open={open}
      onClose={onClose}
      title="География"
      titleBadge={total > 0 ? String(total) : undefined}
      footer={
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              onApply({ regions: selRegions, cities: selCities })
              onClose()
            }}
            className="rounded-lg bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90"
          >
            Сохранить
          </button>
          <button
            type="button"
            onClick={() => {
              setSelRegions([])
              setSelCities([])
            }}
            className="rounded-lg px-3 py-2 text-sm font-medium text-muted transition-colors hover:text-foreground"
          >
            Сбросить
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="relative">
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
          >
            <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
            <path d="M20 20l-3.5-3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Регион или город"
            aria-label="Поиск региона или города"
            className="h-10 w-full rounded-lg border border-input bg-surface pl-9 pr-3 text-sm text-foreground placeholder:text-muted focus:border-ring focus:outline-none focus:ring-2 focus:ring-ring/40"
          />
        </div>

        {/* выбранное — чипы */}
        <div className="flex flex-wrap gap-1.5">
          {allSelected ? (
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-panel px-3 py-1.5 text-sm font-medium text-foreground">
              Все регионы и города
            </span>
          ) : (
            <>
              {selRegions.map((r) => (
                <button
                  key={`reg-${r}`}
                  type="button"
                  onClick={() => toggleRegion(r)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-panel px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-border"
                  title="Убрать"
                >
                  {r} <span aria-hidden className="text-muted">×</span>
                </button>
              ))}
              {selCities.map((c) => (
                <button
                  key={`city-${c}`}
                  type="button"
                  onClick={() => toggleCity(c)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-accent/10 px-3 py-1.5 text-sm font-medium text-accent transition-colors hover:bg-accent/20"
                  title="Убрать город"
                >
                  {c} <span aria-hidden>×</span>
                </button>
              ))}
            </>
          )}
        </div>

        {/* найденные города */}
        {cityResults.length > 0 ? (
          <div className="rounded-lg border border-border">
            <p className="border-b border-border px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
              Города
            </p>
            <ul>
              {cityResults.map((c) => {
                const checked = selCities.includes(c.value)
                return (
                  <li key={c.value}>
                    <label className="flex cursor-pointer items-center gap-2.5 px-3 py-2 text-sm text-foreground hover:bg-surface">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleCity(c.value)}
                        className="h-4 w-4 accent-current"
                      />
                      <span>{c.label}</span>
                      <span className="ml-auto font-mono text-[10px] text-muted">{c.hint}</span>
                    </label>
                  </li>
                )
              })}
            </ul>
          </div>
        ) : null}

        {/* «Все регионы и города» + список регионов */}
        <div>
          <label className="flex cursor-pointer items-center gap-2.5 border-b border-border py-2.5 text-sm font-medium text-foreground">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={() => {
                setSelRegions([])
                setSelCities([])
              }}
              className="h-4 w-4 accent-current"
            />
            Все регионы и города
          </label>
          <ul>
            {shownRegions.map((r) => {
              const checked = allSelected || selRegions.includes(r)
              return (
                <li key={r} className="border-b border-border/50 last:border-0">
                  <label className="flex cursor-pointer items-center gap-2.5 py-2.5 text-sm text-foreground hover:bg-surface">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleRegion(r)}
                      className="h-4 w-4 accent-current"
                    />
                    {r}
                  </label>
                </li>
              )
            })}
            {shownRegions.length === 0 && cityResults.length === 0 ? (
              <li className="py-3 text-sm text-muted">Ничего не найдено</li>
            ) : null}
          </ul>
        </div>
      </div>
    </SideDrawer>
  )
}
