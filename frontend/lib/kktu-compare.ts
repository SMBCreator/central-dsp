// C29: сверка локального справочника ККТУ (lib/kktu.json) с dict/kktu ОРД.
import type { KktuL1 } from "./kktu";

export type OrdKktuItem = {
  code: string;
  name: string;
  level?: number | null;
  parent_code?: string | null;
  usable: boolean;
  deleted: boolean;
};

export type KktuDiff = {
  /** Листья ОРД (действующие, пригодные), которых нет у нас — нельзя выбрать в UI */
  missingLocally: OrdKktuItem[];
  /** Наши листья, которых нет в ОРД или они удалены/непригодны — креатив отклонят */
  invalidLocally: {
    code: string;
    name: string;
    reason: "absent" | "deleted" | "unusable";
  }[];
  /** Совпадающий код, разное название */
  renamed: { code: string; local: string; ord: string }[];
  localLeaves: number;
  ordLeaves: number;
};

const norm = (s: string) =>
  s.trim().toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ");

function isLeaf(item: OrdKktuItem): boolean {
  return item.level != null
    ? item.level === 3
    : item.code.split(".").length === 3;
}

export function compareKktu(tree: KktuL1[], ord: OrdKktuItem[]): KktuDiff {
  const local = new Map<string, string>();
  for (const l1 of tree)
    for (const l2 of l1.children)
      for (const leaf of l2.children) local.set(leaf.code, leaf.name);

  const ordByCode = new Map(ord.map((i) => [i.code, i]));
  const ordLeaves = ord.filter((i) => isLeaf(i) && i.usable && !i.deleted);

  const missingLocally = ordLeaves.filter((i) => !local.has(i.code));
  const invalidLocally: KktuDiff["invalidLocally"] = [];
  const renamed: KktuDiff["renamed"] = [];
  for (const [code, name] of local) {
    const o = ordByCode.get(code);
    if (!o) invalidLocally.push({ code, name, reason: "absent" });
    else if (o.deleted) invalidLocally.push({ code, name, reason: "deleted" });
    else if (!o.usable) invalidLocally.push({ code, name, reason: "unusable" });
    else if (norm(o.name) !== norm(name))
      renamed.push({ code, local: name, ord: o.name });
  }
  const byCode = (a: { code: string }, b: { code: string }) =>
    a.code.localeCompare(b.code, undefined, { numeric: true });
  return {
    missingLocally: missingLocally.sort(byCode),
    invalidLocally: invalidLocally.sort(byCode),
    renamed: renamed.sort(byCode),
    localLeaves: local.size,
    ordLeaves: ordLeaves.length,
  };
}
