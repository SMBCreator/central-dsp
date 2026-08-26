"use client"

import { useEffect, useRef, useState } from "react"
import { Field, inputCls, Panel } from "@/components/ui"
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

/** Full DSP-grade targeting editor. Value maps 1:1 to the backend targeting JSONB. */
export function TargetingEditor({
  value,
  onChange,
}: {
  value: Targeting
  onChange: (t: Targeting) => void
}) {
  const set = (patch: Partial<Targeting>) => onChange({ ...value, ...patch })
  const { data: sspList } = useSWR<{ id: string; name: string; status: string }[]>("/supply-partners", fetcher)
  const { data: audList } = useSWR<{ id: string; name: string; members: number; is_active: boolean }[]>("/audiences", fetcher)
  const ruSelected = (value.geo_countries || []).includes("RU")

  return (
    <div className="space-y-4">
      <Field label="Гео-таргетинг" hint="Поиск: страна, регион или город — от 2 букв. Пусто — весь мир">
        <div className="space-y-2">
          <GeoSearch
            onPick={(it) => {
              if (it.kind === "country") set({ geo_countries: addUnique(value.geo_countries, it.value) })
              else if (it.kind === "region") set({ geo_regions: addUnique(value.geo_regions, it.value) })
              else set({ geo_cities: addUnique(value.geo_cities, it.value) })
            }}
          />
          <div className="flex flex-wrap gap-1.5">
            {COUNTRIES.map((c) => (
              <Chip key={c} active={(value.geo_countries || []).includes(c)} onClick={() => set({ geo_countries: toggle(value.geo_countries, c) })}>
                {c}
              </Chip>
            ))}
          </div>
          {(value.geo_countries?.length || value.geo_regions?.length || value.geo_cities?.length) ? (
            <div className="flex flex-wrap gap-1.5 rounded-md border border-border bg-surface p-2">
              {(value.geo_countries || []).map((c) => (
                <button
                  key={`c-${c}`}
                  type="button"
                  onClick={() => set({ geo_countries: removeVal(value.geo_countries, c) })}
                  className="inline-flex items-center gap-1 rounded-full bg-primary px-2.5 py-0.5 text-xs font-medium text-primary-foreground"
                  title="Убрать страну"
                >
                  {c} <span aria-hidden>×</span>
                </button>
              ))}
              {(value.geo_regions || []).map((r) => (
                <button
                  key={`r-${r}`}
                  type="button"
                  onClick={() => set({ geo_regions: removeVal(value.geo_regions, r) })}
                  className="inline-flex items-center gap-1 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs font-medium text-accent"
                  title="Убрать регион"
                >
                  {r} <span aria-hidden>×</span>
                </button>
              ))}
              {(value.geo_cities || []).map((c) => (
                <button
                  key={`ct-${c}`}
                  type="button"
                  onClick={() => set({ geo_cities: removeVal(value.geo_cities, c) })}
                  className="inline-flex items-center gap-1 rounded-full bg-panel px-2.5 py-0.5 text-xs font-medium text-foreground"
                  title="Убрать город"
                >
                  {c} <span aria-hidden>×</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </Field>

      <Field label="Устройства" hint="Пусто — все устройства">
        <div className="flex flex-wrap gap-1.5">
          {DEVICES.map((d) => (
            <Chip key={d.value} active={(value.device_types || []).includes(d.value)} onClick={() => set({ device_types: toggle(value.device_types, d.value) })}>
              {d.label}
            </Chip>
          ))}
        </div>
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Операционные системы">
          <div className="flex flex-wrap gap-1.5">
            {OS_LIST.map((o) => (
              <Chip key={o} active={(value.os || []).includes(o)} onClick={() => set({ os: toggle(value.os, o) })}>
                {o}
              </Chip>
            ))}
          </div>
        </Field>
        <Field label="Браузеры">
          <div className="flex flex-wrap gap-1.5">
            {BROWSERS.map((b) => (
              <Chip key={b} active={(value.browsers || []).includes(b)} onClick={() => set({ browsers: toggle(value.browsers, b) })}>
                {b}
              </Chip>
            ))}
          </div>
        </Field>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Тип соединения">
          <div className="flex flex-wrap gap-1.5">
            {CONNECTIONS.map((c) => (
              <Chip key={c.value} active={(value.connection_types || []).includes(c.value)} onClick={() => set({ connection_types: toggle(value.connection_types, c.value) })}>
                {c.label}
              </Chip>
            ))}
          </div>
        </Field>
        <Field label="Языки" hint="Пусто — все языки">
          <div className="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
            {LANGUAGES.map(([code, label]) => (
              <Chip key={code} active={(value.languages || []).includes(code)} onClick={() => set({ languages: toggle(value.languages, code) })}>
                {label}
              </Chip>
            ))}
          </div>
        </Field>
      </div>

      <Field label="Тематика площадок (IAB)" hint="Пусто — все категории">
        <Panel className="max-h-40 overflow-y-auto p-2">
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(IAB_CATEGORIES).map(([code, label]) => (
              <Chip key={code} active={(value.iab_categories || []).includes(code)} onClick={() => set({ iab_categories: toggle(value.iab_categories, code) })}>
                {code} · {label}
              </Chip>
            ))}
          </div>
        </Panel>
      </Field>

      {ruSelected ? (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-700 dark:text-amber-400">
          Выбрана Россия: по закону о рекламе перед запуском потребуется маркировка — заполните
          данные рекламодателя (наименование, ИНН, адрес), договор и получите erid в разделе
          «Маркировка (ОРД)». Кампания на РФ без erid не активируется.
        </div>
      ) : null}

      <Field label="SSP-источники" hint="Пусто — все подключённые SSP">
        <div className="flex flex-wrap gap-1.5">
          {(sspList || []).map((sp) => (
            <Chip
              key={sp.id}
              active={(value.ssp_allowlist || []).includes(sp.id)}
              onClick={() => set({ ssp_allowlist: toggle(value.ssp_allowlist, sp.id) })}
            >
              {sp.name}
            </Chip>
          ))}
          {sspList && sspList.length === 0 ? (
            <span className="text-xs text-muted">Нет подключённых SSP — добавьте в разделе «SSP-партнёры»</span>
          ) : null}
        </div>
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Аудитории: показывать только им" hint="Ретаргетинг по сегментам пикселя; пусто — все">
          <div className="flex flex-wrap gap-2">
            {(audList || []).filter((a) => a.is_active).map((a) => (
              <Chip
                key={a.id}
                active={(value.audience_include || []).includes(a.id)}
                onClick={() => set({ audience_include: toggle(value.audience_include, a.id) })}
              >
                {a.name} ({a.members})
              </Chip>
            ))}
            {audList && audList.length === 0 ? (
              <span className="text-xs text-muted">Нет сегментов — создайте в разделе «Аудитории»</span>
            ) : null}
          </div>
        </Field>
        <Field label="Аудитории: исключить" hint="Например, уже купившие">
          <div className="flex flex-wrap gap-2">
            {(audList || []).filter((a) => a.is_active).map((a) => (
              <Chip
                key={a.id}
                active={(value.audience_exclude || []).includes(a.id)}
                onClick={() => set({ audience_exclude: toggle(value.audience_exclude, a.id) })}
              >
                {a.name}
              </Chip>
            ))}
          </div>
        </Field>
      </div>

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
          <div className="flex flex-wrap gap-1.5 pt-1.5">
            {GENDERS.map((g) => (
              <Chip key={g.value} active={(value.genders || []).includes(g.value)} onClick={() => set({ genders: toggle(value.genders, g.value) })}>
                {g.label}
              </Chip>
            ))}
          </div>
        </Field>
      </div>

      <Field label="Дни недели" hint="Пусто — все дни">
        <div className="flex flex-wrap gap-1.5">
          {DAYS.map((d, i) => (
            <Chip key={d} active={(value.days || []).includes(i)} onClick={() => set({ days: toggle(value.days, i) })}>
              {d}
            </Chip>
          ))}
        </div>
      </Field>

      <Field label="Часы показа (UTC)" hint="Пусто — круглосуточно">
        <div className="flex flex-wrap gap-1">
          {Array.from({ length: 24 }, (_, h) => (
            <Chip key={h} active={(value.hours || []).includes(h)} onClick={() => set({ hours: toggle(value.hours, h) })}>
              {String(h).padStart(2, "0")}
            </Chip>
          ))}
        </div>
      </Field>

      {/* --- Inventory environment --- */}
      <div className="border-t border-border pt-4">
        <p className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
          Инвентарь и качество трафика
        </p>
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
            <Field label="Мин. версия ОС" hint="Напр.: 14 или 12.5">
              <input
                className={inputCls}
                defaultValue={value.os_version_min ?? ""}
                onBlur={(e) => set({ os_version_min: e.target.value.trim() || undefined })}
                placeholder="Любая"
              />
            </Field>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Операторы связи" hint="Пусто — все операторы">
              <div className="flex flex-wrap gap-1.5">
                {CARRIER_PRESETS.map((c) => (
                  <Chip key={c} active={(value.carriers || []).includes(c)} onClick={() => set({ carriers: toggle(value.carriers, c) })}>
                    {c}
                  </Chip>
                ))}
                {(value.carriers || [])
                  .filter((c) => !CARRIER_PRESETS.includes(c))
                  .map((c) => (
                    <Chip key={c} active onClick={() => set({ carriers: removeVal(value.carriers, c) })}>
                      {c} ×
                    </Chip>
                  ))}
              </div>
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
        </div>
      </div>

      {/* --- Slot-level --- */}
      <div className="border-t border-border pt-4">
        <p className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
          Рекламный слот
        </p>
        <div className="space-y-4">
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
        </div>
      </div>

      {/* --- Regulatory --- */}
      <div className="border-t border-border pt-4">
        <p className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
          Регуляторика и приватность
        </p>
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
      </div>
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
