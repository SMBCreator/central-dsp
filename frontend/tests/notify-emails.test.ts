import { describe, expect, it } from "vitest"
import { emailsError, formatEmails, parseEmails } from "@/lib/notify-emails"

describe("parseEmails", () => {
  it("splits on commas, semicolons, whitespace; lowercases; dedups", () => {
    const p = parseEmails(" A@x.io, a@x.io;\nb@y.org  c@z.dev ")
    expect(p.emails).toEqual(["a@x.io", "b@y.org", "c@z.dev"])
    expect(p.invalid).toEqual([])
    expect(emailsError(p)).toBeNull()
  })
  it("reports invalid addresses without dropping valid ones", () => {
    const p = parseEmails("ok@x.io nope also@bad")
    expect(p.emails).toEqual(["ok@x.io"])
    expect(p.invalid).toEqual(["nope", "also@bad"])
    expect(emailsError(p)).toMatch(/Некорректный email: nope, also@bad/)
  })
  it("caps at 20 addresses", () => {
    const p = parseEmails(Array.from({ length: 21 }, (_, i) => `u${i}@x.io`).join(","))
    expect(p.tooMany).toBe(true)
    expect(emailsError(p)).toMatch(/Не больше 20/)
  })
  it("round-trips through formatEmails", () => {
    expect(parseEmails(formatEmails(["a@x.io", "b@y.org"])).emails).toEqual(["a@x.io", "b@y.org"])
    expect(formatEmails(null)).toBe("")
  })
})
