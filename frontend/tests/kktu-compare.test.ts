import { describe, expect, it } from "vitest";
import { compareKktu, type OrdKktuItem } from "@/lib/kktu-compare";
import { KKTU_TREE, type KktuL1 } from "@/lib/kktu";

const tree: KktuL1[] = [
  {
    code: "1",
    name: "A",
    children: [
      {
        code: "1.1",
        name: "AA",
        children: [
          { code: "1.1.1", name: "Вино" },
          { code: "1.1.2", name: "Пиво" },
          { code: "1.1.3", name: "Сидр" },
          { code: "1.1.4", name: "Квас" },
        ],
      },
    ],
  },
];

const item = (
  code: string,
  name: string,
  extra: Partial<OrdKktuItem> = {},
): OrdKktuItem => ({
  code,
  name,
  level: code.split(".").length,
  usable: true,
  deleted: false,
  ...extra,
});

describe("compareKktu", () => {
  it("classifies differences", () => {
    const d = compareKktu(tree, [
      item("1", "A", { usable: false }),
      item("1.1", "AA", { usable: false }),
      item("1.1.1", "вино "),
      item("1.1.2", "Пиво", { deleted: true }),
      item("1.1.3", "Сидр", { usable: false }),
      item("1.1.5", "Медовуха"),
      item("1.1.6", "Старое", { deleted: true }),
    ]);
    expect(d.missingLocally.map((i) => i.code)).toEqual(["1.1.5"]);
    expect(d.invalidLocally).toEqual([
      { code: "1.1.2", name: "Пиво", reason: "deleted" },
      { code: "1.1.3", name: "Сидр", reason: "unusable" },
      { code: "1.1.4", name: "Квас", reason: "absent" },
    ]);
    expect(d.renamed).toEqual([]);
    expect(d.localLeaves).toBe(4);
    expect(d.ordLeaves).toBe(2);
  });

  it("detects renamed leaves", () => {
    const d = compareKktu(tree, [item("1.1.1", "Вино игристое")]);
    expect(d.renamed).toEqual([
      { code: "1.1.1", local: "Вино", ord: "Вино игристое" },
    ]);
  });

  it("real tree has 294 leaves and matches itself", () => {
    const ord = KKTU_TREE.flatMap((l1) =>
      l1.children.flatMap((l2) => l2.children.map((l) => item(l.code, l.name))),
    );
    const d = compareKktu(KKTU_TREE, ord);
    expect(d.localLeaves).toBe(294);
    expect(d.missingLocally).toHaveLength(0);
    expect(d.invalidLocally).toHaveLength(0);
    expect(d.renamed).toHaveLength(0);
  });
});
