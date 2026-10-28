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

/* Города по регионам — раскрываются стрелкой у региона (как на Авито) */
export const REGION_CITIES: Record<string, string[]> = {
  "Москва": ["Зеленоград"],
  "Московская область": ["Балашиха", "Подольск", "Химки", "Мытищи", "Королёв", "Люберцы", "Красногорск", "Одинцово", "Домодедово", "Щёлково", "Серпухов", "Коломна"],
  "Санкт-Петербург": ["Колпино", "Пушкин", "Петергоф", "Кронштадт"],
  "Ленинградская область": ["Гатчина", "Выборг", "Всеволожск", "Сосновый Бор", "Тихвин", "Кириши"],
  "Краснодарский край": ["Краснодар", "Сочи", "Новороссийск", "Армавир", "Анапа", "Геленджик", "Ейск", "Туапсе"],
  "Свердловская область": ["Екатеринбург", "Нижний Тагил", "Каменск-Уральский", "Первоуральск", "Серов"],
  "Новосибирская область": ["Новосибирск", "Бердск", "Искитим", "Куйбышев"],
  "Татарстан": ["Казань", "Набережные Челны", "Нижнекамск", "Альметьевск", "Зеленодольск"],
  "Нижегородская область": ["Нижний Новгород", "Дзержинск", "Арзамас", "Саров", "Бор"],
  "Челябинская область": ["Челябинск", "Магнитогорск", "Златоуст", "Миасс", "Копейск"],
  "Самарская область": ["Самара", "Тольятти", "Сызрань", "Новокуйбышевск"],
  "Башкортостан": ["Уфа", "Стерлитамак", "Салават", "Нефтекамск", "Октябрьский"],
  "Ростовская область": ["Ростов-на-Дону", "Таганрог", "Шахты", "Волгодонск", "Новочеркасск", "Батайск"],
  "Красноярский край": ["Красноярск", "Норильск", "Ачинск", "Канск", "Железногорск"],
  "Пермский край": ["Пермь", "Березники", "Соликамск", "Чайковский"],
  "Воронежская область": ["Воронеж", "Борисоглебск", "Россошь", "Лиски"],
  "Волгоградская область": ["Волгоград", "Волжский", "Камышин", "Михайловка"],
  "Саратовская область": ["Саратов", "Энгельс", "Балаково", "Балашов"],
  "Тюменская область": ["Тюмень", "Тобольск", "Ишим"],
  "Иркутская область": ["Иркутск", "Братск", "Ангарск", "Усть-Илимск"],
  "Омская область": ["Омск", "Тара", "Исилькуль"],
  "Кемеровская область": ["Кемерово", "Новокузнецк", "Прокопьевск", "Междуреченск"],
  "Приморский край": ["Владивосток", "Уссурийск", "Находка", "Артём"],
  "Хабаровский край": ["Хабаровск", "Комсомольск-на-Амуре", "Амурск"],
  "Ставропольский край": ["Ставрополь", "Пятигорск", "Кисловодск", "Невинномысск", "Ессентуки"],
  "Алтайский край": ["Барнаул", "Бийск", "Рубцовск", "Новоалтайск"],
  "Оренбургская область": ["Оренбург", "Орск", "Новотроицк", "Бузулук"],
  "Удмуртия": ["Ижевск", "Сарапул", "Воткинск", "Глазов"],
  "Ярославская область": ["Ярославль", "Рыбинск", "Переславль-Залесский"],
  "Владимирская область": ["Владимир", "Ковров", "Муром", "Александров"],
  "Тульская область": ["Тула", "Новомосковск", "Алексин"],
  "Калининградская область": ["Калининград", "Советск", "Черняховск"],
  "Белгородская область": ["Белгород", "Старый Оскол", "Губкин"],
  "Липецкая область": ["Липецк", "Елец"],
  "Курская область": ["Курск", "Железногорск"],
  "Рязанская область": ["Рязань", "Касимов", "Скопин"],
  "Пензенская область": ["Пенза", "Кузнецк", "Заречный"],
  "Ульяновская область": ["Ульяновск", "Димитровград"],
  "Кировская область": ["Киров", "Кирово-Чепецк", "Слободской"],
  "Тверская область": ["Тверь", "Ржев", "Вышний Волочёк"],
  "Брянская область": ["Брянск", "Клинцы", "Новозыбков"],
  "Ивановская область": ["Иваново", "Кинешма", "Шуя"],
  "Архангельская область": ["Архангельск", "Северодвинск", "Котлас"],
  "Вологодская область": ["Вологда", "Череповец", "Великий Устюг"],
  "Мурманская область": ["Мурманск", "Апатиты", "Североморск"],
  "Томская область": ["Томск", "Северск", "Стрежевой"],
  "Курганская область": ["Курган", "Шадринск"],
  "Астраханская область": ["Астрахань", "Ахтубинск"],
  "Ханты-Мансийский АО": ["Сургут", "Нижневартовск", "Ханты-Мансийск", "Нефтеюганск"],
  "Ямало-Ненецкий АО": ["Новый Уренгой", "Ноябрьск", "Салехард"],
  "Дагестан": ["Махачкала", "Дербент", "Хасавюрт", "Каспийск"],
  "Крым": ["Симферополь", "Керчь", "Евпатория", "Ялта", "Феодосия"],
  "Чечня": ["Грозный", "Урус-Мартан", "Гудермес"],
  "Бурятия": ["Улан-Удэ", "Северобайкальск"],
  "Саха (Якутия)": ["Якутск", "Нерюнгри", "Мирный"],
  "Карелия": ["Петрозаводск", "Кондопога", "Сегежа"],
  "Коми": ["Сыктывкар", "Ухта", "Воркута", "Печора"],
  "Чувашия": ["Чебоксары", "Новочебоксарск", "Канаш"],
  "Мордовия": ["Саранск", "Рузаевка"],
  "Марий Эл": ["Йошкар-Ола", "Волжск"],
  "Смоленская область": ["Смоленск", "Вязьма", "Рославль"],
  "Калужская область": ["Калуга", "Обнинск", "Людиново"],
  "Костромская область": ["Кострома", "Буй", "Шарья"],
  "Орловская область": ["Орёл", "Ливны", "Мценск"],
  "Тамбовская область": ["Тамбов", "Мичуринск", "Моршанск"],
  "Новгородская область": ["Великий Новгород", "Боровичи", "Старая Русса"],
  "Псковская область": ["Псков", "Великие Луки"],
  "Амурская область": ["Благовещенск", "Белогорск", "Свободный"],
  "Забайкальский край": ["Чита", "Краснокаменск", "Борзя"],
  "Камчатский край": ["Петропавловск-Камчатский", "Елизово"],
  "Магаданская область": ["Магадан", "Сусуман"],
  "Сахалинская область": ["Южно-Сахалинск", "Корсаков", "Холмск"],
  "Севастополь": ["Инкерман", "Балаклава"],
  "Кабардино-Балкария": ["Нальчик", "Прохладный", "Баксан"],
  "Северная Осетия": ["Владикавказ", "Моздок", "Беслан"],
  "Ингушетия": ["Магас", "Назрань", "Карабулак"],
  "Карачаево-Черкесия": ["Черкесск", "Усть-Джегута"],
  "Адыгея": ["Майкоп", "Адыгейск"],
  "Калмыкия": ["Элиста", "Лагань"],
  "Хакасия": ["Абакан", "Черногорск", "Саяногорск"],
  "Тыва": ["Кызыл", "Ак-Довурак"],
  "Алтай": ["Горно-Алтайск"],
  "Еврейская АО": ["Биробиджан"],
  "Чукотский АО": ["Анадырь", "Билибино"],
  "Ненецкий АО": ["Нарьян-Мар"],
}

/* Другие страны (кроме России): название → ISO-код для targeting.geo_countries */
export const OTHER_COUNTRIES: { code: string; label: string }[] = [
  { code: "KZ", label: "Казахстан" }, { code: "BY", label: "Беларусь" },
  { code: "UZ", label: "Узбекистан" }, { code: "KG", label: "Киргизия" },
  { code: "AM", label: "Армения" }, { code: "AZ", label: "Азербайджан" },
  { code: "GE", label: "Грузия" }, { code: "MD", label: "Молдова" },
  { code: "TJ", label: "Таджикистан" }, { code: "TM", label: "Туркменистан" },
  { code: "TR", label: "Турция" }, { code: "AE", label: "ОАЭ" },
  { code: "RS", label: "Сербия" }, { code: "IL", label: "Израиль" },
  { code: "DE", label: "Германия" }, { code: "FR", label: "Франция" },
  { code: "IT", label: "Италия" }, { code: "ES", label: "Испания" },
  { code: "PL", label: "Польша" }, { code: "CZ", label: "Чехия" },
  { code: "GB", label: "Великобритания" }, { code: "NL", label: "Нидерланды" },
  { code: "US", label: "США" }, { code: "CA", label: "Канада" },
  { code: "BR", label: "Бразилия" }, { code: "AR", label: "Аргентина" },
  { code: "CN", label: "Китай" }, { code: "JP", label: "Япония" },
  { code: "KR", label: "Южная Корея" }, { code: "IN", label: "Индия" },
  { code: "TH", label: "Таиланд" }, { code: "VN", label: "Вьетнам" },
  { code: "ID", label: "Индонезия" }, { code: "MY", label: "Малайзия" },
  { code: "SG", label: "Сингапур" }, { code: "EG", label: "Египет" },
  { code: "SA", label: "Саудовская Аравия" }, { code: "ZA", label: "ЮАР" },
]

/** Название страны по ISO-коду (для чипов в форме) */
export function countryLabel(code: string): string {
  if (code === "RU") return "Россия"
  return OTHER_COUNTRIES.find((c) => c.code === code)?.label || code
}

type GeoCity = { kind: "country" | "region" | "city"; label: string; value: string; hint: string }

/* ---------- Панель «География» (как на Авито) ---------- */

export function GeoDrawer({
  open,
  onClose,
  regions,
  cities,
  countries = [],
  onApply,
  foreignOnly = false,
}: {
  open: boolean
  onClose: () => void
  regions: string[]
  cities: string[]
  /** ISO-коды выбранных стран, кроме России */
  countries?: string[]
  onApply: (next: { regions: string[]; cities: string[]; countries: string[] }) => void
  /** ТЗ п.5: рекламодатель «за пределами РФ» — регионы и города России не предлагаются. */
  foreignOnly?: boolean
}) {
  const [selRegions, setSelRegions] = useState<string[]>(regions)
  const [selCities, setSelCities] = useState<string[]>(cities)
  const [selCountries, setSelCountries] = useState<string[]>(countries)
  const [expanded, setExpanded] = useState<string[]>([])
  const [countriesOpen, setCountriesOpen] = useState(false)
  const [q, setQ] = useState("")
  const [cityResults, setCityResults] = useState<GeoCity[]>([])
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // при каждом открытии — синхронизация с текущим значением
  useEffect(() => {
    if (open) {
      setSelRegions(foreignOnly ? [] : regions)
      setSelCities(foreignOnly ? [] : cities)
      setSelCountries(countries.filter((c) => c !== "RU"))
      setExpanded([])
      setCountriesOpen(foreignOnly || countries.some((c) => c !== "RU"))
      setQ("")
      setCityResults([])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // поиск городов через backend (регионы фильтруются локально)
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    const query = q.trim()
    if (foreignOnly || query.length < 2) {
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
  }, [q, foreignOnly])

  const query = q.trim().toLowerCase()
  const shownRegions = useMemo(
    () =>
      foreignOnly
        ? []
        : query
        ? RU_REGIONS.filter(
            (r) =>
              r.toLowerCase().includes(query) ||
              (REGION_CITIES[r] || []).some((c) => c.toLowerCase().includes(query)),
          )
        : RU_REGIONS,
    [query, foreignOnly],
  )
  const shownCountries = useMemo(
    () => (query ? OTHER_COUNTRIES.filter((c) => c.label.toLowerCase().includes(query)) : OTHER_COUNTRIES),
    [query],
  )
  const allSelected = selRegions.length === 0 && selCities.length === 0 && selCountries.length === 0
  const total = selRegions.length + selCities.length + selCountries.length

  const toggleRegion = (r: string) =>
    setSelRegions((prev) => (prev.includes(r) ? prev.filter((x) => x !== r) : [...prev, r]))
  const toggleCity = (c: string) =>
    setSelCities((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]))
  const toggleCountry = (code: string) =>
    setSelCountries((prev) => (prev.includes(code) ? prev.filter((x) => x !== code) : [...prev, code]))
  const toggleExpand = (r: string) =>
    setExpanded((prev) => (prev.includes(r) ? prev.filter((x) => x !== r) : [...prev, r]))

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
              onApply({ regions: selRegions, cities: selCities, countries: selCountries })
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
              setSelCountries([])
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
            placeholder={foreignOnly ? "Страна" : "Регион или город"}
            aria-label={foreignOnly ? "Поиск страны" : "Поиск региона или города"}
            className="h-10 w-full rounded-lg border border-input bg-surface pl-9 pr-3 text-sm text-foreground placeholder:text-muted focus:border-ring focus:outline-none focus:ring-2 focus:ring-ring/40"
          />
        </div>

        {/* выбранное — чипы */}
        <div className="flex flex-wrap gap-1.5">
          {allSelected ? (
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-panel px-3 py-1.5 text-sm font-medium text-foreground">
              {foreignOnly ? "Страны не выбраны" : "Все регионы и города"}
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
              {selCountries.map((code) => (
                <button
                  key={`cn-${code}`}
                  type="button"
                  onClick={() => toggleCountry(code)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary/10 px-3 py-1.5 text-sm font-medium text-primary transition-colors hover:bg-primary/20"
                  title="Убрать страну"
                >
                  {countryLabel(code)} <span aria-hidden>×</span>
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

        {/* «Все регионы и города» + список регионов с раскрытием городов */}
        {foreignOnly ? null : (
        <div>
          <label className="flex cursor-pointer items-center gap-2.5 border-b border-border py-2.5 text-sm font-medium text-foreground">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={() => {
                setSelRegions([])
                setSelCities([])
                setSelCountries([])
              }}
              className="h-4 w-4 accent-current"
            />
            Все регионы и города
          </label>
          <ul>
            {shownRegions.map((r) => {
              const checked = allSelected || selRegions.includes(r)
              const regionCities = REGION_CITIES[r] || []
              const isOpen = expanded.includes(r) || (!!query && regionCities.some((c) => c.toLowerCase().includes(query)))
              const shownCities = query ? regionCities.filter((c) => c.toLowerCase().includes(query) || r.toLowerCase().includes(query)) : regionCities
              return (
                <li key={r} className="border-b border-border/50 last:border-0">
                  <div className="flex items-center">
                    <label className="flex flex-1 cursor-pointer items-center gap-2.5 py-2.5 text-sm text-foreground hover:bg-surface">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleRegion(r)}
                        className="h-4 w-4 accent-current"
                      />
                      {r}
                    </label>
                    {regionCities.length > 0 ? (
                      <button
                        type="button"
                        onClick={() => toggleExpand(r)}
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? "Скрыть" : "Показать"} города: ${r}`}
                        className="rounded-md p-1.5 text-muted transition-colors hover:bg-panel hover:text-foreground"
                      >
                        <svg
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          aria-hidden="true"
                          className={`transition-transform ${isOpen ? "rotate-180" : ""}`}
                        >
                          <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </button>
                    ) : null}
                  </div>
                  {isOpen && shownCities.length > 0 ? (
                    <ul className="mb-1.5 ml-6 border-l border-border/60 pl-3">
                      {shownCities.map((c) => {
                        const cityChecked = checked || selCities.includes(c)
                        return (
                          <li key={c}>
                            <label className="flex cursor-pointer items-center gap-2.5 py-1.5 text-sm text-foreground hover:bg-surface">
                              <input
                                type="checkbox"
                                checked={cityChecked}
                                disabled={checked}
                                onChange={() => toggleCity(c)}
                                className="h-4 w-4 accent-current disabled:opacity-50"
                              />
                              <span className={checked ? "text-muted" : undefined}>{c}</span>
                            </label>
                          </li>
                        )
                      })}
                    </ul>
                  ) : null}
                </li>
              )
            })}
            {!foreignOnly && shownRegions.length === 0 && cityResults.length === 0 && shownCountries.length === 0 ? (
              <li className="py-3 text-sm text-muted">Ничего не найдено</li>
            ) : null}
          </ul>
        </div>
        )}

        {/* Другие страны */}
        <div className="rounded-lg border border-border">
          <button
            type="button"
            onClick={() => setCountriesOpen((v) => !v)}
            aria-expanded={countriesOpen || !!query}
            className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-sm font-medium text-foreground"
          >
            <span>
              Другие страны
              {selCountries.length > 0 ? <span className="ml-1.5 text-muted">{selCountries.length}</span> : null}
            </span>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
              className={`text-muted transition-transform ${countriesOpen || query ? "rotate-180" : ""}`}
            >
              <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          {countriesOpen || (query && shownCountries.length > 0) ? (
            <ul className="border-t border-border px-3 py-1">
              {shownCountries.map((c) => (
                <li key={c.code}>
                  <label className="flex cursor-pointer items-center gap-2.5 py-2 text-sm text-foreground hover:bg-surface">
                    <input
                      type="checkbox"
                      checked={selCountries.includes(c.code)}
                      onChange={() => toggleCountry(c.code)}
                      className="h-4 w-4 accent-current"
                    />
                    {c.label}
                  </label>
                </li>
              ))}
              {shownCountries.length === 0 ? <li className="py-2 text-sm text-muted">Не найдено</li> : null}
            </ul>
          ) : null}
        </div>
      </div>
    </SideDrawer>
  )
}
