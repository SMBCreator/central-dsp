"use client"

import { Field, inputCls, Panel } from "@/components/ui"
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

  return (
    <div className="space-y-4">
      <Field label="Гео — страны" hint="Пусто — все страны">
        <div className="flex flex-wrap gap-1.5">
          {COUNTRIES.map((c) => (
            <Chip key={c} active={(value.geo_countries || []).includes(c)} onClick={() => set({ geo_countries: toggle(value.geo_countries, c) })}>
              {c}
            </Chip>
          ))}
        </div>
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Регионы" hint="Через запятую, напр.: Москва, МО">
          <input
            className={inputCls}
            defaultValue={(value.geo_regions || []).join(", ")}
            onBlur={(e) => set({ geo_regions: csv(e.target.value) })}
            placeholder="Все регионы"
          />
        </Field>
        <Field label="Города" hint="Через запятую">
          <input
            className={inputCls}
            defaultValue={(value.geo_cities || []).join(", ")}
            onBlur={(e) => set({ geo_cities: csv(e.target.value) })}
            placeholder="Все города"
          />
        </Field>
      </div>

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
        <Field label="Языки" hint="Коды через запятую: ru, kk, uz">
          <input
            className={inputCls}
            defaultValue={(value.languages || []).join(", ")}
            onBlur={(e) => set({ languages: csv(e.target.value) })}
            placeholder="Все языки"
          />
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
            <Field label="Операторы связи" hint="Через запятую: MTS, Beeline, Megafon">
              <input
                className={inputCls}
                defaultValue={(value.carriers || []).join(", ")}
                onBlur={(e) => set({ carriers: csv(e.target.value) })}
                placeholder="Все операторы"
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
