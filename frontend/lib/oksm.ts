// Частые страны контрагентов, код ОКСМ (= ISO 3166-1 numeric). Любой другой
// трёхзначный код можно ввести вручную. 643 (РФ) намеренно нет: резидент — «Юрлицо».
export const OKSM_COMMON: { code: string; name: string }[] = [
  { code: "112", name: "Беларусь" },
  { code: "398", name: "Казахстан" },
  { code: "051", name: "Армения" },
  { code: "031", name: "Азербайджан" },
  { code: "417", name: "Киргизия" },
  { code: "860", name: "Узбекистан" },
  { code: "762", name: "Таджикистан" },
  { code: "795", name: "Туркменистан" },
  { code: "268", name: "Грузия" },
  { code: "498", name: "Молдова" },
  { code: "156", name: "Китай" },
  { code: "344", name: "Гонконг" },
  { code: "784", name: "ОАЭ" },
  { code: "792", name: "Турция" },
  { code: "196", name: "Кипр" },
  { code: "356", name: "Индия" },
  { code: "702", name: "Сингапур" },
  { code: "704", name: "Вьетнам" },
  { code: "410", name: "Республика Корея" },
  { code: "392", name: "Япония" },
  { code: "376", name: "Израиль" },
  { code: "682", name: "Саудовская Аравия" },
  { code: "818", name: "Египет" },
  { code: "688", name: "Сербия" },
  { code: "276", name: "Германия" },
  { code: "528", name: "Нидерланды" },
  { code: "250", name: "Франция" },
  { code: "380", name: "Италия" },
  { code: "724", name: "Испания" },
  { code: "756", name: "Швейцария" },
  { code: "616", name: "Польша" },
  { code: "826", name: "Великобритания" },
  { code: "840", name: "США" },
]

export const RUSSIA_OKSM = "643"

export type ForeignRequisites = {
  region_number: string
  tax_id: string
  registration_number: string
}

export const EMPTY_FOREIGN: ForeignRequisites = {
  region_number: "",
  tax_id: "",
  registration_number: "",
}

/** Та же проверка, что и на backend (ord.ozon_mapping.foreign_requisites). */
export function foreignRequisitesError(v: ForeignRequisites): string | null {
  const region = v.region_number.trim()
  if (!/^\d{3}$/.test(region)) return "Укажите код страны по ОКСМ (3 цифры)"
  if (region === RUSSIA_OKSM)
    return "643 — это РФ: для резидента выберите тип «Юрлицо»"
  if (!v.tax_id.trim() && !v.registration_number.trim())
    return "Укажите налоговый или регистрационный номер"
  return null
}

/** Тело для POST /ord/counterparties: реквизиты только у иностранного контрагента. */
export function foreignPayload(legalType: string, v: ForeignRequisites) {
  if (legalType !== "foreign") return {}
  return {
    region_number: v.region_number.trim(),
    tax_id: v.tax_id.trim() || null,
    registration_number: v.registration_number.trim() || null,
  }
}
