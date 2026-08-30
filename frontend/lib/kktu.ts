// ККТУ — классификатор категорий товаров и услуг для маркировки рекламы (38-ФЗ).
// Дерево из 30 категорий 1-го уровня → 83 второго → 294 листа (3-й уровень).
// Источник — data/kktu-source.xlsx, сгенерировано в lib/kktu.json (не парсим xlsx в рантайме).

import raw from "./kktu.json"

export type KktuLeaf = { code: string; name: string }
export type KktuL2 = { code: string; name: string; children: KktuLeaf[] }
export type KktuL1 = { code: string; name: string; children: KktuL2[] }

export const KKTU_TREE = raw as KktuL1[]

export type KktuResolved = {
  code: string
  leafName: string
  /** Полный путь «L1 / L2 / L3» — удобно показывать и хранить в kktu_label */
  path: string
  l1: string
  l2: string
}

// Плоский индекс: код листа -> разложение по уровням. Строится один раз.
const INDEX: Map<string, KktuResolved> = (() => {
  const m = new Map<string, KktuResolved>()
  for (const l1 of KKTU_TREE) {
    for (const l2 of l1.children) {
      for (const leaf of l2.children) {
        m.set(leaf.code, {
          code: leaf.code,
          leafName: leaf.name,
          path: `${l1.name} / ${l2.name} / ${leaf.name}`,
          l1: l1.code,
          l2: l2.code,
        })
      }
    }
  }
  return m
})()

/** Разбор кода листа (например «1.1.1») в путь и названия уровней. */
export function resolveKktu(code?: string | null): KktuResolved | null {
  if (!code) return null
  return INDEX.get(code.trim()) ?? null
}

/** Короткая подпись для чипа/таблицы: «1.1.1 — Вино». */
export function kktuShortLabel(code?: string | null): string {
  const r = resolveKktu(code)
  return r ? `${r.code} — ${r.leafName}` : ""
}
