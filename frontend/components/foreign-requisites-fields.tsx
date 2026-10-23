"use client"

import { useId } from "react"
import { Field, inputCls } from "@/components/ui"
import { OKSM_COMMON, type ForeignRequisites } from "@/lib/oksm"

export function ForeignRequisitesFields({
  value,
  onChange,
}: {
  value: ForeignRequisites
  onChange: (next: ForeignRequisites) => void
}) {
  const listId = useId()
  return (
    <fieldset className="space-y-3 rounded-xl border border-border bg-surface px-3 py-3">
      <legend className="px-1 text-xs font-medium text-muted">
        Реквизиты иностранного контрагента (для ОРД)
      </legend>
      <Field
        label="Страна регистрации (код ОКСМ)"
        hint="Начните вводить код или выберите из списка; любой другой трёхзначный код тоже подойдёт."
      >
        <input
          required
          inputMode="numeric"
          pattern="\d{3}"
          maxLength={3}
          list={listId}
          className={inputCls}
          value={value.region_number}
          onChange={(e) =>
            onChange({
              ...value,
              region_number: e.target.value.replace(/\D/g, ""),
            })
          }
          placeholder="398"
        />
        <datalist id={listId}>
          {OKSM_COMMON.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </datalist>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field
          label="Налоговый номер (taxId)"
          hint="Аналог ИНН в стране регистрации"
        >
          <input
            maxLength={64}
            className={inputCls}
            value={value.tax_id}
            onChange={(e) => onChange({ ...value, tax_id: e.target.value })}
          />
        </Field>
        <Field
          label="Регистрационный номер"
          hint="Аналог ОГРН; нужен хотя бы один из номеров"
        >
          <input
            maxLength={64}
            className={inputCls}
            value={value.registration_number}
            onChange={(e) =>
              onChange({ ...value, registration_number: e.target.value })
            }
          />
        </Field>
      </div>
    </fieldset>
  )
}
