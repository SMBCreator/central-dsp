"use client"

import { useEffect, useMemo, useState } from "react"
import { KKTU_TREE, resolveKktu } from "@/lib/kktu"
import { inputCls } from "@/components/ui"

/**
 * Каскадный выбор категории ККТУ (маркировка объявления, 38-ФЗ).
 * Три зависимых списка: 1-й уровень → 2-й → 3-й (лист). Значение наружу —
 * код листа (например «1.1.1») и полный путь для kktu_label.
 */
export function KktuSelect({
  value,
  onChange,
  id,
}: {
  value?: string | null
  onChange: (code: string, label: string) => void
  id?: string
}) {
  // Инициализируем уровни из переданного кода листа
  const initial = useMemo(() => resolveKktu(value), [value])
  const [l1, setL1] = useState(initial?.l1 ?? "")
  const [l2, setL2] = useState(initial?.l2 ?? "")
  const [l3, setL3] = useState(initial?.code ?? "")

  // Синхронизируем, если value пришло/изменилось извне (напр. при редактировании)
  useEffect(() => {
    const r = resolveKktu(value)
    setL1(r?.l1 ?? "")
    setL2(r?.l2 ?? "")
    setL3(r?.code ?? "")
  }, [value])

  const l1Node = KKTU_TREE.find((n) => n.code === l1)
  const l2Node = l1Node?.children.find((n) => n.code === l2)

  const emit = (leafCode: string) => {
    const r = resolveKktu(leafCode)
    onChange(leafCode, r?.path ?? "")
  }

  return (
    <div className="grid gap-2 sm:grid-cols-3">
      <select
        id={id}
        aria-label="ККТУ: категория 1-го уровня"
        className={inputCls}
        value={l1}
        onChange={(e) => {
          setL1(e.target.value)
          setL2("")
          setL3("")
          onChange("", "")
        }}
      >
        <option value="">Категория…</option>
        {KKTU_TREE.map((n) => (
          <option key={n.code} value={n.code}>
            {n.code}. {n.name}
          </option>
        ))}
      </select>

      <select
        aria-label="ККТУ: категория 2-го уровня"
        className={inputCls}
        value={l2}
        disabled={!l1Node}
        onChange={(e) => {
          setL2(e.target.value)
          setL3("")
          onChange("", "")
        }}
      >
        <option value="">Подкатегория…</option>
        {(l1Node?.children ?? []).map((n) => (
          <option key={n.code} value={n.code}>
            {n.code} {n.name}
          </option>
        ))}
      </select>

      <select
        aria-label="ККТУ: категория 3-го уровня"
        className={inputCls}
        value={l3}
        disabled={!l2Node}
        onChange={(e) => {
          setL3(e.target.value)
          emit(e.target.value)
        }}
      >
        <option value="">Уточнение…</option>
        {(l2Node?.children ?? []).map((n) => (
          <option key={n.code} value={n.code}>
            {n.code} {n.name}
          </option>
        ))}
      </select>
    </div>
  )
}
