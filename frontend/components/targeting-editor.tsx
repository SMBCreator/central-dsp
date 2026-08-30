"use client"

import { useEffect, useRef, useState, type ReactNode } from "react"
import { Field, inputCls } from "@/components/ui"
import { MultiSelect } from "@/components/multi-select"
import { GeoDrawer, countryLabel } from "@/components/geo-panel"
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { IAB_CATEGORIES, type Targeting } from "@/lib/types"

export const COUNTRIES = ["RU", "KZ", "BY", "UZ", "KG", "AM", "AZ", "GE", "MD", "TJ", "TM", "UA"]
export const DEVICES = [
  { value: "mobile", label: "Мобильные" },
  { value: "desktop", label: "Десктоп" },
  { value: "tablet", label: "Планшеты" },
  { value: "ctv", label: "Smart TV" },
]
const OS_LIST = ["Android", "iOS", "Windows", "macOS", "Linux"]
const BROWSERS = ["Chrome", "Safari", "Firefox", "Edge", "Opera", "YaBrowser"]
const CONNECTIONS = [
  { value: "wifi", label: "Wi-Fi" },
  { value: "cellular", label: "Мобильная сеть" },
  { value: "ethernet", label: "Ethernet" },
]
const GENDERS = [
  { value: "M", label: "Мужчины" },
  { value: "F", label: "Женщины" },
]
const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
        active ? "bg-primary text-primary-foreground" : "bg-panel text-muted hover:text-foreground"
      }`}
    >
      {children}
    </button>
  )
}

const LANGUAGES: [string, string][] = [
  ["ru", "Русский"], ["en", "Английский"], ["kk", "Казахский"], ["uz", "Узбекский"],
  ["ky", "Киргизский"], ["az", "Азербайджанский"], ["hy", "Армянский"], ["ka", "Грузинский"],
  ["be", "Белорусский"], ["tg", "Таджикский"], ["ro", "Румынский"], ["tr", "Турецкий"],
  ["de", "Немецкий"], ["fr", "Французский"], ["es", "Испанский"], ["ar", "Арабский"],
  ["zh", "Китайский"], ["hi", "Хинди"],
]

const CARRIER_PRESETS = ["MTS", "Beeline", "Megafon", "Tele2", "Yota", "Rostelecom", "Kcell", "Ucell"]

type GeoItem = { kind: "country" | "region" | "city"; label: string; value: string; hint: string }

const GEO_KIND_LABEL: Record<GeoItem["kind"], string> = {
  country: "Страна",
  region: "Регион",
  city: "Город",
}

function GeoSearch({ onPick }: { onPick: (item: GeoItem) => void }) {
  const [q, setQ] = useState("")
  const [items, setItems] = useState<GeoItem[]>([])
  const [open, setOpen] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    const query = q.trim()
    if (query.length < 2) {
      setItems([])
      setOpen(false)
      return
    }
    timer.current = setTimeout(async () => {
      try {
        const res = (await fetcher(`/geo/search?q=${encodeURIComponent(query)}`)) as GeoItem[]
        setItems(res)
        setOpen(true)
      } catch {
        setItems([])
      }
    }, 200)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [q])

  return (
    <div className="relative">
      <input
        className={inputCls}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => items.length > 0 && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder="Начните вводить: Каз, Моск, Екатер…"
        aria-label="Поиск гео: страна, регион или город"
      />
      {open && items.length > 0 ? (
        <ul className="absolute z-20 mt-1 max-h-60 w-full overflow-y-auto rounded-md border border-border bg-card py-1 shadow-lg">
          {items.map((it) => (
            <li key={`${it.kind}:${it.value}`}>
              <button
                type="button"
                className="flex w-full items-center justify-between px-3 py-1.5 text-left text-sm text-foreground hover:bg-surface"
                onMouseDown={(e) => {
                  e.preventDefault()
                  onPick(it)
                  setQ("")
                  setOpen(false)
                }}
              >
                <span>{it.label}</span>
                <span className="ml-2 shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted">
                  {GEO_KIND_LABEL[it.kind]} · {it.hint}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function addUnique(arr: string[] | undefined, v: string): string[] {
  const a = arr || []
  return a.includes(v) ? a : [...a, v]
}

function removeVal(arr: string[] | undefined, v: string): string[] | undefined {
  const a = (arr || []).filter((x) => x !== v)
  return a.length ? a : undefined
}

function toggle<T>(arr: T[] | undefined, v: T): T[] {
  const a = arr || []
  return a.includes(v) ? a.filter((x) => x !== v) : [...a, v]
}

function csv(value: string): string[] | undefined {
  const items = value.split(",").map((s) => s.trim()).filter(Boolean)
  return items.length ? items : undefined
}

/** C1: массив строк -> нормализация в undefined при пустоте (контракт targeting JSONB). */
function norm(next: string[]): string[] | undefined {
  return next.length ? next : undefined
}

/* ---------- Секция таргетинга: плоская, всегда раскрыта (структура как на Авито) ---------- */

function TargetSection({
  title,
  summary,
  onReset,
  defaultOpen: _defaultOpen = false,
  children,
}: {
  title: string
  summary: string
  onReset: () => void
  defaultOpen?: boolean
  children: ReactNode
}) {
  const isDefault = /^(Весь мир|Все устройства и среды|Весь инвентарь|Все пользователи|Круглосуточно|Любой слот|Без ограничений)$/.test(summary)
  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="text-base font-bold text-foreground">{title}</h3>
        {!isDefault ? (
          <button
            type="button"
            onClick={onReset}
            className="shrink-0 rounded px-2 py-1 text-xs font-medium text-muted transition-colors hover:text-danger"
            title="Сбросить все поля секции"
          >
            Сбросить
          </button>
        ) : null}
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  )
}

/* ---------- сводки секций (для свернутого вида) ---------- */

function geoSummary(t: Targeting): string {
  const parts: string[] = []
  if (t.geo_countries?.length) parts.push(t.geo_countries.join(", "))
  if (t.geo_regions?.length) parts.push(`${t.geo_regions.length} регион(ов)`)
  if (t.geo_cities?.length) parts.push(`${t.geo_cities.length} город(ов)`)
  return parts.join(" · ") || "Весь мир"
}

function deviceEnvSummary(t: Targeting): string {
  const parts: string[] = []
  if (t.device_types?.length)
    parts.push(t.device_types.map((d) => DEVICES.find((x) => x.value === d)?.label || d).join(", "))
  if (t.os?.length) parts.push(`ОС: ${t.os.join(", ")}`)
  if (t.browsers?.length) parts.push(`Браузеры: ${t.browsers.length}`)
  if (t.languages?.length) parts.push(`Языки: ${t.languages.length}`)
  if (t.connection_types?.length) parts.push(`Сеть: ${t.connection_types.length}`)
  if (t.carriers?.length) parts.push(`Операторы: ${t.carriers.length}`)
  return parts.join(" · ") || "Все устройства и среды"
}

function inventorySummary(t: Targeting): string {
  const parts: string[] = []
  if (t.ssp_allowlist?.length) parts.push(`SSP: ${t.ssp_allowlist.length}`)
  if (t.iab_categories?.length) parts.push(`IAB: ${t.iab_categories.length}`)
  if (t.traffic_types?.length) parts.push(t.traffic_types.includes("app") ? "Приложения" : "Web")
  if (t.domain_allowlist?.length) parts.push(`allow×${t.domain_allowlist.length}`)
  if (t.domain_blocklist?.length) parts.push(`block×${t.domain_blocklist.length}`)
  if (t.keywords_allow?.length) parts.push(`слова×${t.keywords_allow.length}`)
  return parts.join(" · ") || "Весь инвентарь"
}

function audienceSummary(t: Targeting): string {
  const parts: string[] = []
  if (t.audience_include?.length) parts.push(`вкл: ${t.audience_include.length}`)
  if (t.audience_exclude?.length) parts.push(`искл: ${t.audience_exclude.length}`)
  if (t.age_min || t.age_max) parts.push(`${t.age_min || 13}–${t.age_max || 99} лет`)
  if (t.genders?.length) parts.push(t.genders.map((g) => (g === "M" ? "М" : "Ж")).join("/"))
  return parts.join(" · ") || "Все пользователи"
}

function timeSummary(t: Targeting): string {
  const parts: string[] = []
  if (t.days?.length) parts.push(`${t.days.length} дн/нед`)
  if (t.hours?.length) parts.push(`${t.hours.length} ч/сут`)
  return parts.join(" · ") || "Круглосуточно"
}

function slotSummary(t: Targeting): string {
  const parts: string[] = []
  if (t.banner_positions?.length) parts.push("позиция")
  if (t.interstitial_only) parts.push("interstitial")
  if (t.exclude_interstitial) parts.push("без interstitial")
  if (t.rewarded_only) parts.push("rewarded")
  if (t.secure_only) parts.push("HTTPS")
  if (t.video_skippable_only) parts.push("skippable")
  if (t.deal_ids?.length) parts.push(`PMP×${t.deal_ids.length}`)
  return parts.join(" · ") || "Любой слот"
}

function regulatorySummary(t: Targeting): string {
  const parts: string[] = []
  if (t.block_coppa) parts.push("без COPPA")
  if (t.block_gdpr) parts.push("без GDPR")
  if (t.skip_dnt) parts.push("без DNT")
  return parts.join(" · ") || "Без ограничений"
}

/** Full DSP-grade targeting editor. Value maps 1:1 to the backend targeting JSONB.
 *  C2: секции-аккордеоны со сводками; C1: MultiSelect для множественного выбора.
 *  Все переменные таргетинга сохранены — изменилось только представление. */
export function TargetingEditor({
  value,
  onChange,
}: {
  value: Targeting
  onChange: (t: Targeting) => void
}) {
  const set = (patch: Partial<Targeting>) => onChange({ ...value, ...patch })
  const [geoOpen, setGeoOpen] = useState(false)
  const { data: sspList } = useSWR<{ id: string; name: string; status: string }[]>("/supply-partners", fetcher)
  const { data: audList } = useSWR<{ id: string; name: string; members: number; is_active: boolean }[]>("/audiences", fetcher)
  const ruSelected =
    (value.geo_countries || []).includes("RU") || !!value.geo_regions?.length || !!value.geo_cities?.length
  const otherCountries = (value.geo_countries || []).filter((c) => c !== "RU")

  return (
    <div className="space-y-3">
      {/* --- География: поле-триггер + выезжающая справа панель (как на Авито) --- */}
      <TargetSection
        title="География"
        summary={geoSummary(value)}
        defaultOpen
        onReset={() => set({ geo_countries: undefined, geo_regions: undefined, geo_cities: undefined })}
      >
        <Field label="Регионы и города показа" hint="Нажмите на поле — справа откроется список регионов и городов России. Пусто — показ без гео-ограничений">
          <button
            type="button"
            onClick={() => setGeoOpen(true)}
            aria-haspopup="dialog"
            className="flex min-h-10 w-full items-center justify-between gap-2 rounded-lg border border-input bg-card px-2 py-1.5 text-left transition-colors hover:border-muted"
          >
            <span className="flex min-w-0 flex-wrap items-center gap-1.5">
              {!value.geo_regions?.length && !value.geo_cities?.length && !otherCountries.length ? (
                <span className="rounded-md bg-panel px-2.5 py-1 text-sm font-medium text-foreground">
                  Все регионы и города
                </span>
              ) : (
                <>
                  {(value.geo_regions || []).map((r) => (
                    <span
                      key={`r-${r}`}
                      role="button"
                      tabIndex={0}
                      onClick={(e) => {
                        e.stopPropagation()
                        set({ geo_regions: removeVal(value.geo_regions, r) })
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault()
                          e.stopPropagation()
                          set({ geo_regions: removeVal(value.geo_regions, r) })
                        }
                      }}
                      className="inline-flex cursor-pointer items-center gap-1 rounded-md bg-panel px-2.5 py-1 text-sm font-medium text-foreground hover:bg-border"
                      title="Убрать регион"
                    >
                      {r} <span aria-hidden className="text-muted">×</span>
                    </span>
                  ))}
                  {(value.geo_cities || []).map((c) => (
                    <span
                      key={`ct-${c}`}
                      role="button"
                      tabIndex={0}
                      onClick={(e) => {
                        e.stopPropagation()
                        set({ geo_cities: removeVal(value.geo_cities, c) })
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault()
                          e.stopPropagation()
                          set({ geo_cities: removeVal(value.geo_cities, c) })
                        }
                      }}
                      className="inline-flex cursor-pointer items-center gap-1 rounded-md bg-accent/10 px-2.5 py-1 text-sm font-medium text-accent hover:bg-accent/20"
                      title="Убрать город"
                    >
                      {c} <span aria-hidden>×</span>
                    </span>
                  ))}
                  {otherCountries.map((code) => (
                    <span
                      key={`cn-${code}`}
                      role="button"
                      tabIndex={0}
                      onClick={(e) => {
                        e.stopPropagation()
                        set({ geo_countries: removeVal(value.geo_countries, code) })
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault()
                          e.stopPropagation()
                          set({ geo_countries: removeVal(value.geo_countries, code) })
                        }
                      }}
                      className="inline-flex cursor-pointer items-center gap-1 rounded-md bg-primary/10 px-2.5 py-1 text-sm font-medium text-primary hover:bg-primary/20"
                      title="Убрать страну"
                    >
                      {countryLabel(code)} <span aria-hidden>×</span>
                    </span>
                  ))}
                </>
              )}
            </span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0 text-muted">
              <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </Field>
        <GeoDrawer
          open={geoOpen}
          onClose={() => setGeoOpen(false)}
          regions={value.geo_regions || []}
          cities={value.geo_cities || []}
          countries={otherCountries}
          onApply={({ regions, cities, countries }) => {
            // страны: выбранные «другие» + Россия, если заданы её регионы/города
            const ru = regions.length || cities.length ? ["RU"] : []
            const next = [...ru, ...countries]
            set({
              geo_regions: regions.length ? regions : undefined,
              geo_cities: cities.length ? cities : undefined,
              geo_countries: next.length ? next : undefined,
            })
          }}
        />
        {ruSelected ? (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-700 dark:text-amber-400">
            Выбрана Россия: по закону о рекламе перед запуском потребуется маркировка — заполните
            данные рекламодателя (наименование, ИНН, адрес), договор и получите erid в разделе
            «Маркировка (ОРД)». Кампания на РФ без erid не активируется.
          </div>
        ) : null}
      </TargetSection>

      {/* --- Устройства и среда --- */}
      <TargetSection
        title="Устройства и среда"
        summary={deviceEnvSummary(value)}
        onReset={() =>
          set({
            device_types: undefined, os: undefined, browsers: undefined, languages: undefined,
            connection_types: undefined, carriers: undefined, os_version_min: undefined, ip_ranges: undefined,
          })
        }
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Тип устройства" hint="Пусто — все устройства">
            <MultiSelect
              options={DEVICES}
              selected={value.device_types || []}
              onChange={(next) => set({ device_types: norm(next) })}
            />
          </Field>
          <Field label="Операционные системы">
            <MultiSelect
              options={OS_LIST.map((o) => ({ value: o, label: o }))}
              selected={value.os || []}
              onChange={(next) => set({ os: norm(next) })}
            />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Браузеры">
            <MultiSelect
              options={BROWSERS.map((b) => ({ value: b, label: b }))}
              selected={value.browsers || []}
              onChange={(next) => set({ browsers: norm(next) })}
            />
          </Field>
          <Field label="Языки" hint="Пусто — все языки">
            <MultiSelect
              options={LANGUAGES.map(([code, label]) => ({ value: code, label, hint: code }))}
              selected={value.languages || []}
              onChange={(next) => set({ languages: norm(next) })}
            />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Тип соединения">
            <MultiSelect
              options={CONNECTIONS}
              selected={value.connection_types || []}
              onChange={(next) => set({ connection_types: norm(next) })}
            />
          </Field>
          <Field label="Операторы связи" hint="Пусто — все операторы">
            <MultiSelect
              options={CARRIER_PRESETS.map((c) => ({ value: c, label: c }))}
              selected={value.carriers || []}
              onChange={(next) => set({ carriers: norm(next) })}
            />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Мин. версия ОС" hint="Напр.: 14 или 12.5">
            <input
              className={inputCls}
              defaultValue={value.os_version_min ?? ""}
              onBlur={(e) => set({ os_version_min: e.target.value.trim() || undefined })}
              placeholder="Любая"
            />
          </Field>
          <Field label="IP-префиксы" hint="Напр.: 95.24., 178.176.">
            <input
              className={inputCls}
              defaultValue={(value.ip_ranges || []).join(", ")}
              onBlur={(e) => set({ ip_ranges: csv(e.target.value) })}
              placeholder="Все IP"
            />
          </Field>
        </div>
      </TargetSection>

      {/* --- Инвентарь --- */}
      <TargetSection
        title="Инвентарь"
        summary={inventorySummary(value)}
        onReset={() =>
          set({
            ssp_allowlist: undefined, iab_categories: undefined, traffic_types: undefined,
            domain_allowlist: undefined, domain_blocklist: undefined,
            app_bundles_allow: undefined, app_bundles_block: undefined,
            publisher_allow: undefined, publisher_block: undefined,
            keywords_allow: undefined, keywords_block: undefined,
          })
        }
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="SSP-источники" hint="Пусто — все подключённые SSP">
            {sspList && sspList.length === 0 ? (
              <span className="text-xs text-muted">Нет подключённых SSP — добавьте в разделе «SSP-партнёры»</span>
            ) : (
              <MultiSelect
                options={(sspList || []).map((sp) => ({ value: sp.id, label: sp.name }))}
                selected={value.ssp_allowlist || []}
                onChange={(next) => set({ ssp_allowlist: norm(next) })}
              />
            )}
          </Field>
          <Field label="Тематика площадок (IAB)" hint="Пусто — все категории">
            <MultiSelect
              options={Object.entries(IAB_CATEGORIES).map(([code, label]) => ({ value: code, label: `${code} · ${label}` }))}
              selected={value.iab_categories || []}
              onChange={(next) => set({ iab_categories: norm(next) })}
            />
          </Field>
        </div>
        <Field label="Тип инвентаря" hint="Пусто — web и приложения">
          <div className="flex flex-wrap gap-1.5">
            {[
              { value: "site", label: "Web-сайты" },
              { value: "app", label: "Приложения" },
            ].map((t) => (
              <Chip key={t.value} active={(value.traffic_types || []).includes(t.value)} onClick={() => set({ traffic_types: toggle(value.traffic_types, t.value) })}>
                {t.label}
              </Chip>
            ))}
          </div>
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Белый список доменов" hint="Через запятую; пусто — все площадки">
            <input
              className={inputCls}
              defaultValue={(value.domain_allowlist || []).join(", ")}
              onBlur={(e) => set({ domain_allowlist: csv(e.target.value) })}
              placeholder="site1.ru, site2.kz"
            />
          </Field>
          <Field label="Чёрный список доменов">
            <input
              className={inputCls}
              defaultValue={(value.domain_blocklist || []).join(", ")}
              onBlur={(e) => set({ domain_blocklist: csv(e.target.value) })}
              placeholder="badsite.ru"
            />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="App-бандлы (белый список)" hint="com.app.one, id123456">
            <input
              className={inputCls}
              defaultValue={(value.app_bundles_allow || []).join(", ")}
              onBlur={(e) => set({ app_bundles_allow: csv(e.target.value) })}
              placeholder="Все приложения"
            />
          </Field>
          <Field label="App-бандлы (чёрный список)">
            <input
              className={inputCls}
              defaultValue={(value.app_bundles_block || []).join(", ")}
              onBlur={(e) => set({ app_bundles_block: csv(e.target.value) })}
              placeholder="—"
            />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Паблишеры (белый список)" hint="ID паблишеров из bid request">
            <input
              className={inputCls}
              defaultValue={(value.publisher_allow || []).join(", ")}
              onBlur={(e) => set({ publisher_allow: csv(e.target.value) })}
              placeholder="Все паблишеры"
            />
          </Field>
          <Field label="Паблишеры (чёрный список)">
            <input
              className={inputCls}
              defaultValue={(value.publisher_block || []).join(", ")}
              onBlur={(e) => set({ publisher_block: csv(e.target.value) })}
              placeholder="—"
            />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Ключевые слова (контекст)" hint="Показывать на страницах с этими словами">
            <input
              className={inputCls}
              defaultValue={(value.keywords_allow || []).join(", ")}
              onBlur={(e) => set({ keywords_allow: csv(e.target.value) })}
              placeholder="Любой контекст"
            />
          </Field>
          <Field label="Стоп-слова" hint="Не показывать при наличии этих слов">
            <input
              className={inputCls}
              defaultValue={(value.keywords_block || []).join(", ")}
              onBlur={(e) => set({ keywords_block: csv(e.target.value) })}
              placeholder="—"
            />
          </Field>
        </div>
      </TargetSection>

      {/* --- Аудитории и демография --- */}
      <TargetSection
        title="Аудитории"
        summary={audienceSummary(value)}
        onReset={() =>
          set({
            audience_include: undefined, audience_exclude: undefined,
            age_min: undefined, age_max: undefined, genders: undefined,
          })
        }
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Показывать только им" hint="Ретаргетинг по сегментам пикселя; пусто — все">
            {audList && audList.length === 0 ? (
              <span className="text-xs text-muted">Нет сегментов — создайте в разделе «Аудитории»</span>
            ) : (
              <MultiSelect
                options={(audList || []).filter((a) => a.is_active).map((a) => ({ value: a.id, label: a.name, hint: String(a.members) }))}
                selected={value.audience_include || []}
                onChange={(next) => set({ audience_include: norm(next) })}
              />
            )}
          </Field>
          <Field label="Исключить" hint="Например, уже купившие">
            <MultiSelect
              options={(audList || []).filter((a) => a.is_active).map((a) => ({ value: a.id, label: a.name }))}
              selected={value.audience_exclude || []}
              onChange={(next) => set({ audience_exclude: norm(next) })}
            />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field label="Возраст от">
            <input
              type="number"
              min={13}
              max={99}
              className={inputCls}
              defaultValue={value.age_min ?? ""}
              onBlur={(e) => set({ age_min: e.target.value ? Number(e.target.value) : undefined })}
              placeholder="13"
            />
          </Field>
          <Field label="Возраст до">
            <input
              type="number"
              min={13}
              max={99}
              className={inputCls}
              defaultValue={value.age_max ?? ""}
              onBlur={(e) => set({ age_max: e.target.value ? Number(e.target.value) : undefined })}
              placeholder="99"
            />
          </Field>
          <Field label="Пол">
            <div className="grid grid-cols-3 gap-1.5">
              <button
                type="button"
                onClick={() => set({ genders: undefined })}
                className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                  !value.genders?.length
                    ? "border-foreground bg-card text-foreground"
                    : "border-border bg-panel text-muted hover:text-foreground"
                }`}
              >
                Любой
              </button>
              {GENDERS.map((g) => (
                <button
                  key={g.value}
                  type="button"
                  onClick={() => set({ genders: toggle(value.genders, g.value) })}
                  className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                    (value.genders || []).includes(g.value)
                      ? "border-foreground bg-card text-foreground"
                      : "border-border bg-panel text-muted hover:text-foreground"
                  }`}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </Field>
        </div>
      </TargetSection>

      {/* --- Время показа --- */}
      <TargetSection
        title="Время показа"
        summary={timeSummary(value)}
        onReset={() => set({ days: undefined, hours: undefined })}
      >
        <Field label="Дни недели" hint="Пусто — все дни">
          <div className="flex flex-wrap gap-1.5">
            {DAYS.map((d, i) => (
              <Chip key={d} active={(value.days || []).includes(i)} onClick={() => set({ days: toggle(value.days, i) })}>
                {d}
              </Chip>
            ))}
          </div>
        </Field>
        <Field label="Часы показа (UTC)" hint="Пусто — круглосуточно. МСК = UTC+3">
          <div className="flex flex-wrap gap-1">
            {Array.from({ length: 24 }, (_, h) => (
              <Chip key={h} active={(value.hours || []).includes(h)} onClick={() => set({ hours: toggle(value.hours, h) })}>
                {String(h).padStart(2, "0")}
              </Chip>
            ))}
          </div>
        </Field>
      </TargetSection>

      {/* --- Рекламный слот --- */}
      <TargetSection
        title="Рекламный слот"
        summary={slotSummary(value)}
        onReset={() =>
          set({
            banner_positions: undefined, interstitial_only: undefined, exclude_interstitial: undefined,
            rewarded_only: undefined, secure_only: undefined, video_skippable_only: undefined,
            video_min_duration: undefined, deal_ids: undefined,
          })
        }
      >
        <Field label="Позиция на странице" hint="Пусто — любая позиция">
          <div className="flex flex-wrap gap-1.5">
            {[
              { value: 1, label: "Первый экран (above the fold)" },
              { value: 3, label: "Ниже первого экрана" },
            ].map((p) => (
              <Chip key={p.value} active={(value.banner_positions || []).includes(p.value)} onClick={() => set({ banner_positions: toggle(value.banner_positions, p.value) })}>
                {p.label}
              </Chip>
            ))}
          </div>
        </Field>
        <div className="flex flex-wrap gap-1.5">
          <Chip active={!!value.interstitial_only} onClick={() => set({ interstitial_only: !value.interstitial_only || undefined, exclude_interstitial: undefined })}>
            Только Interstitial
          </Chip>
          <Chip active={!!value.exclude_interstitial} onClick={() => set({ exclude_interstitial: !value.exclude_interstitial || undefined, interstitial_only: undefined })}>
            Исключить Interstitial
          </Chip>
          <Chip active={!!value.rewarded_only} onClick={() => set({ rewarded_only: !value.rewarded_only || undefined })}>
            Только Rewarded
          </Chip>
          <Chip active={!!value.secure_only} onClick={() => set({ secure_only: !value.secure_only || undefined })}>
            Только HTTPS
          </Chip>
          <Chip active={!!value.video_skippable_only} onClick={() => set({ video_skippable_only: !value.video_skippable_only || undefined })}>
            Видео: только skippable
          </Chip>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Мин. длительность видео-слота, сек" hint="Для видео-кампаний">
            <input
              type="number"
              min={0}
              className={inputCls}
              defaultValue={value.video_min_duration ?? ""}
              onBlur={(e) => set({ video_min_duration: e.target.value ? Number(e.target.value) : undefined })}
              placeholder="Любая"
            />
          </Field>
          <Field label="PMP-сделки (Deal ID)" hint="Ставки только по этим приватным сделкам">
            <input
              className={inputCls}
              defaultValue={(value.deal_ids || []).join(", ")}
              onBlur={(e) => set({ deal_ids: csv(e.target.value) })}
              placeholder="Открытый аукцион"
            />
          </Field>
        </div>
      </TargetSection>

      {/* --- Регуляторика --- */}
      <TargetSection
        title="Регуляторика и приватность"
        summary={regulatorySummary(value)}
        onReset={() => set({ block_coppa: undefined, block_gdpr: undefined, skip_dnt: undefined })}
      >
        <div className="flex flex-wrap gap-1.5">
          <Chip active={!!value.block_coppa} onClick={() => set({ block_coppa: !value.block_coppa || undefined })}>
            Пропускать COPPA (детский трафик)
          </Chip>
          <Chip active={!!value.block_gdpr} onClick={() => set({ block_gdpr: !value.block_gdpr || undefined })}>
            Пропускать GDPR-трафик
          </Chip>
          <Chip active={!!value.skip_dnt} onClick={() => set({ skip_dnt: !value.skip_dnt || undefined })}>
            Пропускать DNT / LMT устройства
          </Chip>
        </div>
      </TargetSection>
    </div>
  )
}

/** Short human-readable summary of a targeting object for tables. */
export function targetingSummary(t: Targeting): string {
  const parts: string[] = []
  if (t.geo_countries?.length) parts.push(t.geo_countries.join(", "))
  if (t.geo_cities?.length) parts.push(t.geo_cities.slice(0, 2).join(", "))
  if (t.device_types?.length) parts.push(t.device_types.map((d) => DEVICES.find((x) => x.value === d)?.label || d).join(", "))
  if (t.os?.length) parts.push(t.os.join("/"))
  if (t.iab_categories?.length) parts.push(`IAB×${t.iab_categories.length}`)
  if (t.age_min || t.age_max) parts.push(`${t.age_min || 13}–${t.age_max || 99} лет`)
  if (t.genders?.length) parts.push(t.genders.join("/"))
  if (t.hours?.length) parts.push(`${t.hours.length} ч/сут`)
  if (t.days?.length) parts.push(`${t.days.length} дн/нед`)
  if (t.domain_allowlist?.length) parts.push(`allow×${t.domain_allowlist.length}`)
  if (t.domain_blocklist?.length) parts.push(`block×${t.domain_blocklist.length}`)
  return parts.join(" · ") || "Без ограничений"
}
