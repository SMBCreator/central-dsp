import { describe, expect, it } from "vitest"
import { EMPTY_FOREIGN, foreignPayload, foreignRequisitesError, OKSM_COMMON, RUSSIA_OKSM } from "@/lib/oksm"

describe("C30: реквизиты иностранного контрагента", () => {
  const ok = { region_number: "398", tax_id: "123456789012", registration_number: "" }

  it("принимает страну и хотя бы один номер", () => {
    expect(foreignRequisitesError(ok)).toBeNull()
    expect(foreignRequisitesError({ ...ok, tax_id: "", registration_number: "R-1" })).toBeNull()
  })

  it("отклоняет то же, что и backend", () => {
    expect(foreignRequisitesError(EMPTY_FOREIGN)).toMatch(/ОКСМ/)
    expect(foreignRequisitesError({ ...ok, region_number: "39" })).toMatch(/ОКСМ/)
    expect(foreignRequisitesError({ ...ok, region_number: RUSSIA_OKSM })).toMatch(/РФ/)
    expect(foreignRequisitesError({ ...ok, tax_id: "  " })).toMatch(/номер/)
  })

  it("шлёт реквизиты только для иностранного типа", () => {
    expect(foreignPayload("ul", ok)).toEqual({})
    expect(foreignPayload("foreign", { ...ok, tax_id: " T " })).toEqual({
      region_number: "398",
      tax_id: "T",
      registration_number: null,
    })
  })

  it("справочник: трёхзначные коды без РФ и без дублей", () => {
    const codes = OKSM_COMMON.map((c) => c.code)
    expect(codes.every((c) => /^\d{3}$/.test(c))).toBe(true)
    expect(codes).not.toContain(RUSSIA_OKSM)
    expect(new Set(codes).size).toBe(codes.length)
  })
})
