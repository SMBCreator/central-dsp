"use client"

import { useId, useState, type KeyboardEvent, type ClipboardEvent } from "react"

type Props = {
  value: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  /** Приводить к нижнему регистру (контекстные слова матчатся без учёта регистра в биддере). */
  lowercase?: boolean
  /** Максимальная длина одного тега; длиннее — обрезается. */
  maxTagLength?: number
  /** Ограничение на число тегов; лишние отбрасываются с подсказкой. */
  maxTags?: number
  /** aria-label поля ввода; если не задан — берётся из placeholder. */
  ariaLabel?: string
  disabled?: boolean
}

const SEPARATORS = /[,\n;]+/

function normalize(raw: string, lowercase: boolean, maxTagLength: number): string {
  const t = raw.trim().replace(/\s+/g, " ").slice(0, maxTagLength)
  return lowercase ? t.toLowerCase() : t
}

/**
 * Ввод списка тегов: Enter / запятая / точка с запятой / вставка списка — фиксируют тег,
 * Backspace на пустом поле удаляет последний. Дубликаты схлопываются.
 * Внешний контракт — string[] (пустой массив = «не задано»).
 */
export function TagInput({
  value,
  onChange,
  placeholder,
  lowercase = false,
  maxTagLength = 64,
  maxTags = 500,
  ariaLabel,
  disabled,
}: Props) {
  const [draft, setDraft] = useState("")
  const [notice, setNotice] = useState<string | null>(null)
  const listId = useId()

  const commit = (raw: string) => {
    const parts = raw.split(SEPARATORS).map((p) => normalize(p, lowercase, maxTagLength)).filter(Boolean)
    if (!parts.length) return
    const seen = new Set(value)
    const next = [...value]
    let dup = 0
    for (const p of parts) {
      if (seen.has(p)) {
        dup += 1
        continue
      }
      if (next.length >= maxTags) {
        setNotice(`Не больше ${maxTags} значений`)
        break
      }
      seen.add(p)
      next.push(p)
    }
    if (dup && next.length === value.length) setNotice(dup === 1 ? "Уже добавлено" : `Уже добавлены (${dup})`)
    else if (dup) setNotice(`Пропущено дубликатов: ${dup}`)
    else setNotice(null)
    if (next.length !== value.length) onChange(next)
    setDraft("")
  }

  const remove = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx))
    setNotice(null)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    if (e.key === "Enter" || e.key === "," || e.key === ";") {
      e.preventDefault()
      commit(draft)
      return
    }
    if (e.key === "Backspace" && draft === "" && value.length) {
      e.preventDefault()
      remove(value.length - 1)
    }
  }

  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData("text")
    if (SEPARATORS.test(text)) {
      e.preventDefault()
      commit(draft + text)
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <div
        className={`flex min-h-9 w-full flex-wrap items-center gap-1.5 rounded-lg border border-input bg-card px-2 py-1 text-sm transition-[border-color,box-shadow] focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/40 ${
          disabled ? "opacity-60" : ""
        }`}
      >
        {value.length ? (
          <ul id={listId} className="contents" aria-label="Добавленные значения">
            {value.map((tag, i) => (
              <li
                key={tag}
                className="inline-flex max-w-full items-center gap-1 rounded-full bg-panel px-2.5 py-0.5 text-xs font-medium text-foreground"
              >
                <span className="truncate">{tag}</span>
                {disabled ? null : (
                  <button
                    type="button"
                    onClick={() => remove(i)}
                    aria-label={`Удалить «${tag}»`}
                    className="-mr-1 rounded-full p-0.5 text-muted transition-colors hover:bg-border hover:text-foreground"
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
                      <path d="M18 6L6 18M6 6l12 12" />
                    </svg>
                  </button>
                )}
              </li>
            ))}
          </ul>
        ) : null}
        <input
          value={draft}
          disabled={disabled}
          onChange={(e) => {
            setDraft(e.target.value)
            if (notice) setNotice(null)
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={() => draft.trim() && commit(draft)}
          placeholder={value.length ? "" : placeholder}
          aria-label={ariaLabel || placeholder || "Добавить значение"}
          aria-describedby={notice ? `${listId}-notice` : undefined}
          className="min-w-24 flex-1 bg-transparent px-1 py-0.5 text-sm text-foreground placeholder:text-muted focus:outline-none"
        />
      </div>
      <p id={`${listId}-notice`} className="text-[11px] leading-4 text-muted" aria-live="polite">
        {notice ?? (
          <>
            Enter или запятая — добавить, Backspace — удалить последнее
            {value.length ? ` · ${value.length}` : ""}
          </>
        )}
      </p>
    </div>
  )
}
