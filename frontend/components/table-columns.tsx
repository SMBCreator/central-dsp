"use client"

/**
 * Настройка столбцов таблиц в стиле Авито:
 * модалка «Настройте столбцы» с чекбоксами, компоновкой (порядок стрелками)
 * и сохранением; кнопка-шестерёнка для панели инструментов таблицы.
 * Используется на страницах «Кампании» и «Статистика».
 */

import { useEffect, useState } from "react"
import { Button, Modal } from "@/components/ui"

export type ColumnMeta = { key: string; label: string }

/**
 * Хук видимости и порядка столбцов с сохранением в браузере.
 * Возвращает упорядоченный список активных ключей и сеттер.
 */
export function useTableColumns(
  storageKey: string,
  allKeys: string[],
  defaults: string[],
): [string[], (keys: string[]) => void] {
  const [cols, setCols] = useState<string[]>(defaults)
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(storageKey)
      if (raw) {
        const parsed = JSON.parse(raw) as string[]
        if (Array.isArray(parsed) && parsed.length) {
          const valid = parsed.filter((k) => allKeys.includes(k))
          if (valid.length) setCols(valid)
        }
      }
    } catch {
      // повреждённые данные игнорируем
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey])
  const save = (keys: string[]) => {
    setCols(keys)
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(keys))
    } catch {
      // приватный режим и т.п.
    }
  }
  return [cols, save]
}

/** Кнопка-шестерёнка: открывает модалку настройки столбцов */
export function ColumnsButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted transition-colors hover:text-foreground"
      aria-label="Настроить столбцы"
      title="Настроить столбцы"
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M12 9a3 3 0 100 6 3 3 0 000-6zm7.5 3a7.5 7.5 0 01-.1 1.2l2 1.6-2 3.4-2.4-1a7.6 7.6 0 01-2 1.2L14.6 21H9.4l-.4-2.6a7.6 7.6 0 01-2-1.2l-2.4 1-2-3.4 2-1.6a7.7 7.7 0 010-2.4l-2-1.6 2-3.4 2.4 1a7.6 7.6 0 012-1.2L9.4 3h5.2l.4 2.6a7.6 7.6 0 012 1.2l2.4-1 2 3.4-2 1.6c.06.4.1.8.1 1.2z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  )
}

/**
 * Модалка «Настройте столбцы» (как на Авито): чекбоксы видимости,
 * первые (закреплённые) столбцы недоступны для отключения,
 * стрелки вверх/вниз меняют порядок выбранных, «Сохранить» применяет.
 */
export function ColumnsModal({
  open,
  onClose,
  all,
  locked = [],
  value,
  onSave,
}: {
  open: boolean
  onClose: () => void
  /** Все доступные столбцы в исходном порядке */
  all: ColumnMeta[]
  /** Подписи закреплённых столбцов (всегда включены, показываются серыми) */
  locked?: string[]
  /** Текущий упорядоченный список активных ключей */
  value: string[]
  onSave: (keys: string[]) => void
}) {
  const [draft, setDraft] = useState<string[]>(value)
  useEffect(() => {
    if (open) setDraft(value)
  }, [open, value])

  const toggle = (key: string) =>
    setDraft((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]))

  const move = (key: string, dir: -1 | 1) =>
    setDraft((prev) => {
      const i = prev.indexOf(key)
      const j = i + dir
      if (i < 0 || j < 0 || j >= prev.length) return prev
      const next = [...prev]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })

  // Отображение: сначала выбранные в их порядке (с возможностью двигать), затем остальные
  const chosen = draft.map((k) => all.find((c) => c.key === k)).filter(Boolean) as ColumnMeta[]
  const rest = all.filter((c) => !draft.includes(c.key))

  return (
    <Modal open={open} onClose={onClose} title="Настройте столбцы">
      <p className="-mt-2 mb-3 text-sm text-muted">Выберите, что показывать в таблице, и задайте порядок</p>
      <div className="max-h-[55vh] space-y-0.5 overflow-y-auto pr-1">
        {locked.map((label) => (
          <div key={label} className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-muted">
            <input type="checkbox" checked disabled className="h-4 w-4 rounded border-border opacity-50" />
            {label}
          </div>
        ))}
        {chosen.map((c, i) => (
          <div key={c.key} className="flex items-center gap-2.5 rounded-md px-2 py-1 text-sm text-foreground hover:bg-panel">
            <label className="flex flex-1 cursor-pointer items-center gap-2.5 py-0.5">
              <input
                type="checkbox"
                checked
                onChange={() => toggle(c.key)}
                className="h-4 w-4 rounded border-border accent-current"
              />
              {c.label}
            </label>
            <span className="flex items-center">
              <button
                type="button"
                onClick={() => move(c.key, -1)}
                disabled={i === 0}
                aria-label={`Переместить «${c.label}» выше`}
                className="rounded p-1 text-muted transition-colors hover:text-foreground disabled:opacity-30"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M18 15l-6-6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
              <button
                type="button"
                onClick={() => move(c.key, 1)}
                disabled={i === chosen.length - 1}
                aria-label={`Переместить «${c.label}» ниже`}
                className="rounded p-1 text-muted transition-colors hover:text-foreground disabled:opacity-30"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            </span>
          </div>
        ))}
        {rest.length > 0 && chosen.length > 0 ? <div className="my-1 border-t border-border" /> : null}
        {rest.map((c) => (
          <label
            key={c.key}
            className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-foreground hover:bg-panel"
          >
            <input
              type="checkbox"
              checked={false}
              onChange={() => toggle(c.key)}
              className="h-4 w-4 rounded border-border accent-current"
            />
            {c.label}
          </label>
        ))}
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button
          onClick={() => {
            onSave(draft)
            onClose()
          }}
        >
          Сохранить
        </Button>
      </div>
    </Modal>
  )
}
