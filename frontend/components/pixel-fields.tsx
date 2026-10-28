"use client"

import { useState } from "react"
import { MacroUrlInput } from "@/components/macro-picker"
import { isHttpUrl } from "@/lib/creative-batch"

const BAD_URL = "Укажите адрес вида https://…"

function PixelField({
  id,
  label,
  value,
  onChange,
  placeholder,
  inputClassName,
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  placeholder: string
  inputClassName?: string
}) {
  const bad = value.trim() !== "" && !isHttpUrl(value)
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-[11px] font-medium text-muted">
        {label}
      </label>
      <MacroUrlInput id={id} value={value} onChange={onChange} kind="pixel" placeholder={placeholder} inputClassName={inputClassName} invalid={bad} />
      {bad ? (
        <p role="alert" className="mt-1 text-[11px] text-danger">
          {BAD_URL}
        </p>
      ) : null}
    </div>
  )
}

interface Props {
  idPrefix: string
  /** Заголовок сворачиваемого блока. */
  title: string
  imp: string
  click: string
  onImp: (v: string) => void
  onClick: (v: string) => void
  /** Общие значения для подсказки в placeholder («Общий: …»). */
  sharedImp?: string
  sharedClick?: string
  inputClassName?: string
  defaultOpen?: boolean
}

/** Сворачиваемый блок «пиксель показа / пиксель клика» с панелью макросов. */
export function PixelFieldsBlock({ idPrefix, title, imp, click, onImp, onClick, sharedImp = "", sharedClick = "", inputClassName, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen)
  const marks = [imp.trim() ? "показ" : "", click.trim() ? "клик" : ""].filter(Boolean)
  const regionId = `${idPrefix}-pixels`

  return (
    <div className="rounded-md border border-border bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={regionId}
        className="flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-[11px] font-medium text-foreground hover:bg-panel focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <span>
          {title}
          {marks.length > 0 ? <span className="ml-2 rounded bg-primary/10 px-1.5 py-0.5 text-primary">{marks.join(", ")}</span> : null}
        </span>
        <span aria-hidden="true" className="text-muted">
          {open ? "▴" : "▾"}
        </span>
      </button>
      {open ? (
        <div id={regionId} className="space-y-2.5 border-t border-border px-2.5 py-2.5">
          <PixelField
            id={`${idPrefix}-imp`}
            label="Пиксель показа"
            value={imp}
            onChange={onImp}
            placeholder={sharedImp ? `Общий: ${sharedImp}` : "https://tracker.example/imp?cb=${CDSP_CACHEBUSTER}"}
            inputClassName={inputClassName}
          />
          <PixelField
            id={`${idPrefix}-click`}
            label="Пиксель клика"
            value={click}
            onChange={onClick}
            placeholder={sharedClick ? `Общий: ${sharedClick}` : "https://tracker.example/click?cb=${CDSP_CACHEBUSTER}"}
            inputClassName={inputClassName}
          />
        </div>
      ) : null}
    </div>
  )
}
