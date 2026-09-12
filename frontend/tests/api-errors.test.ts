import { describe, expect, it } from "vitest"
import { ApiError, describeApiError, ORD_LOCKED_MESSAGE } from "@/lib/api"

describe("describeApiError", () => {
  it("maps 409 ord_locked to the ORD-locked message", () => {
    const err = new ApiError(409, "Креативу уже выдан erid", { code: "ord_locked", fields: ["contract_id"] })
    expect(describeApiError(err)).toBe(ORD_LOCKED_MESSAGE)
  })
  it("keeps the API message for other structured errors", () => {
    const err = new ApiError(422, "Сумма разбивки не сходится", { code: "split_mismatch" })
    expect(describeApiError(err)).toBe("Сумма разбивки не сходится")
  })
  it("falls back for non-Error values", () => {
    expect(describeApiError("boom", "Ошибка")).toBe("Ошибка")
    expect(describeApiError(new Error("x"))).toBe("x")
  })
})
