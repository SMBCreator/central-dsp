"use client"

import { useCallback, useId, useLayoutEffect, useMemo, useRef, useState } from "react"
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { HelpTip, inputCls } from "@/components/ui"
import {
  FALLBACK_MACROS,
  MACRO_API_PATH,
  MACRO_GROUPS,
  MACRO_TEMPLATES,
  appendParam,
  appendParams,
  canAppend,
  hasParam,
  insecurePixelWarning,
  insertAtCaret,
  previewParts,
  unknownMacros,
  type MacroEntry,
} from "@/lib/url-macros"

/** Каталог с сервера; до ответа и при ошибке — запасной из url-macros-catalog.ts. */
export function useMacroCatalog(): MacroEntry[] {
  const { data } = useSWR<MacroEntry[]>(MACRO_API_PATH, fetcher, { revalidateOnFocus: false, dedupingInterval: 10 * 60_000, shouldRetryOnError: false })
  return Array.isArray(data) && data.length > 0 ? data : FALLBACK_MACROS
}

export type MacroFieldKind = "click" | "pixel"

const NEED_ADDRESS = "Сначала введите адрес https://…"

const chipBase =
  "rounded-md border px-2 py-0.5 text-[11px] font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"

interface Props {
  id: string
  value: string
  onChange: (value: string) => void
  kind: MacroFieldKind
  placeholder?: string
  /** Класс поля ввода; по умолчанию общий inputCls. */
  inputClassName?: string
  /** Подпись для скринридера, если рядом нет <label htmlFor>. */
  ariaLabel?: string
  invalid?: boolean
}

/** Поле с подписью в стиле Field, но с label[for]: внутри label нет кнопок панели макросов. */
export function MacroField({ label, hint, ...props }: Props & { label: string; hint?: string }) {
  return (
    <div>
      <label htmlFor={props.id} className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-foreground">
        {label}
        {hint ? <HelpTip text={hint} /> : null}
      </label>
      <MacroUrlInput {...props} />
    </div>
  )
}

/** Поле ссылки/пикселя с панелью макросов и предпросмотром. */
export function MacroUrlInput({ id, value, onChange, kind, placeholder, inputClassName = inputCls, ariaLabel, invalid }: Props) {
  const catalog = useMacroCatalog()
  const inputRef = useRef<HTMLInputElement>(null)
  const pendingCaret = useRef<number | null>(null)
  const [open, setOpen] = useState(false)
  const [onlyMacro, setOnlyMacro] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const panelId = useId()

  useLayoutEffect(() => {
    if (pendingCaret.current === null) return
    const el = inputRef.current
    const pos = pendingCaret.current
    pendingCaret.current = null
    if (el) {
      el.focus()
      el.setSelectionRange(pos, pos)
    }
  }, [value])

  const apply = useCallback(
    (next: string) => {
      setHint(null)
      onChange(next)
    },
    [onChange],
  )

  const addMacro = (entry: MacroEntry) => {
    if (onlyMacro) {
      const el = inputRef.current
      const r = insertAtCaret(value, el?.selectionStart, entry.macro, el?.selectionEnd)
      pendingCaret.current = r.caret
      apply(r.value)
      return
    }
    if (!canAppend(value)) return setHint(NEED_ADDRESS)
    if (hasParam(value, entry.param)) return
    apply(appendParam(value, entry.param, entry.macro))
  }

  const addTemplate = (keys: string[]) => {
    if (!canAppend(value)) return setHint(NEED_ADDRESS)
    apply(appendParams(value, keys, catalog))
  }

  const hasMacroText = value.includes("${")
  const unknown = useMemo(() => (hasMacroText ? unknownMacros(value, catalog) : []), [hasMacroText, value, catalog])
  const parts = useMemo(() => (hasMacroText ? previewParts(value, catalog) : []), [hasMacroText, value, catalog])
  const warning = kind === "pixel" ? insecurePixelWarning(value) : null
  const groups = useMemo(
    () => MACRO_GROUPS.map((g) => ({ ...g, items: catalog.filter((e) => e.group === g.key) })).filter((g) => g.items.length > 0),
    [catalog],
  )

  const copyPreview = async () => {
    try {
      await navigator.clipboard.writeText(parts.map((p) => p.text).join(""))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="space-y-1.5">
      <input
        ref={inputRef}
        id={id}
        type="url"
        value={value}
        onChange={(e) => apply(e.target.value)}
        className={inputClassName}
        placeholder={placeholder}
        aria-label={ariaLabel}
        aria-invalid={invalid || undefined}
        autoComplete="off"
        spellCheck={false}
      />

      {warning ? (
        <p role="status" className="text-[11px] leading-relaxed text-warning">
          {warning}
        </p>
      ) : null}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        className="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-primary hover:bg-panel focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        {open ? "Макросы ▴" : "Макросы ▾"}
      </button>

      {open ? (
        <div id={panelId} className="space-y-2.5 rounded-lg border border-border bg-surface p-2.5">
          <label className="flex items-center gap-2 text-[11px] text-foreground">
            <input type="checkbox" checked={onlyMacro} onChange={(e) => setOnlyMacro(e.target.checked)} />
            Вставить только макрос в позицию курсора
          </label>

          <div>
            <p className="mb-1 text-[11px] font-medium text-muted">Готовые шаблоны</p>
            <div className="flex flex-wrap gap-1.5">
              {MACRO_TEMPLATES.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => addTemplate(t.keys)}
                  title={`Добавит: ${t.hint}`}
                  aria-label={`Шаблон «${t.label}»: добавить ${t.hint}`}
                  className={`${chipBase} border-primary/40 bg-primary/5 text-primary hover:bg-primary/10`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {groups.map((g) => (
            <div key={g.key}>
              <p className="mb-1 text-[11px] font-medium text-muted">{g.label}</p>
              <div className="flex flex-wrap gap-1.5">
                {g.items.map((e) => {
                  const added = !onlyMacro && hasParam(value, e.param)
                  return (
                    <button
                      key={e.key}
                      type="button"
                      onClick={() => addMacro(e)}
                      aria-pressed={added}
                      aria-label={added ? `${e.label}: уже добавлен` : `Добавить ${e.label}: ${e.macro}`}
                      title={`${e.macro}\n${e.description}\nПример: ${e.example}`}
                      className={`${chipBase} ${
                        added ? "border-success/40 bg-success/10 text-success" : "border-border bg-card text-foreground hover:bg-panel"
                      }`}
                    >
                      {e.label}
                      {added ? " · добавлен" : ""}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}

          <p className="text-[11px] leading-relaxed text-muted">
            {"Макросы SSP (например ${AUCTION_PRICE}) подставляются самой SSP, работают только в пикселях, не в ссылке перехода."}
          </p>
        </div>
      ) : null}

      {hint ? (
        <p role="status" className="text-[11px] text-warning">
          {hint}
        </p>
      ) : null}

      {hasMacroText ? (
        <div className="rounded-md border border-border bg-surface px-2.5 py-1.5">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] font-medium text-muted">Как будет выглядеть</p>
            <button
              type="button"
              onClick={copyPreview}
              className="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-primary hover:bg-panel focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            >
              {copied ? "Скопировано" : "Копировать"}
            </button>
          </div>
          <p className="mt-0.5 break-all font-mono text-[11px] leading-relaxed text-foreground" data-macro-preview>
            {parts.map((p, i) =>
              p.kind === "unknown" ? (
                <mark
                  key={i}
                  className="rounded bg-danger/10 px-0.5 text-danger"
                  title={p.suggestion ? `Не из каталога, возможно: ${p.suggestion}` : "Не из каталога"}
                >
                  {p.text}
                </mark>
              ) : p.kind === "macro" ? (
                <span key={i} className="rounded bg-primary/10 px-0.5 text-primary">
                  {p.text}
                </span>
              ) : (
                <span key={i}>{p.text}</span>
              ),
            )}
          </p>
          {unknown.length > 0 ? (
            <ul role="status" className="mt-1 space-y-0.5 text-[11px] text-danger">
              {unknown.map((u) => (
                <li key={u.token}>
                  {u.token} — не из каталога{u.suggestion ? `, возможно: ${"${CDSP_"}${u.suggestion}}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
