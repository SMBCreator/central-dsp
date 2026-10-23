"use client";

import { useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { KKTU_TREE } from "@/lib/kktu";
import { compareKktu, type OrdKktuItem } from "@/lib/kktu-compare";
import {
  Button,
  Card,
  CardHeader,
  Field,
  HelpTip,
  inputCls,
} from "@/components/ui";
import { KktuSelect } from "@/components/kktu-select";

type StepStatus = "ok" | "error" | "skipped";

type SandboxStep = {
  key: string;
  title: string;
  status: StepStatus;
  http_status?: number;
  ms?: number;
  error?: string;
  erid?: string | null;
  request?: { method: string; path: string; body: unknown };
  response?: string;
  kktu?: OrdKktuItem[];
};

type SandboxRun = {
  run_id: string;
  base_url: string;
  period: string;
  entities: {
    advertiser: { external_id: string; inn: string };
    self: { external_id: string; inn: string };
    contract: string;
    creative: string;
    invoice: string;
  };
  summary: Record<StepStatus, number>;
  steps: SandboxStep[];
};

const STATUS_STYLE: Record<StepStatus, string> = {
  ok: "bg-success/10 text-success",
  error: "bg-danger/10 text-danger",
  skipped: "bg-muted/10 text-muted",
};
const STATUS_LABEL: Record<StepStatus, string> = {
  ok: "Успешно",
  error: "Ошибка",
  skipped: "Пропущен",
};

function StepRow({ step, index }: { step: SandboxStep; index: number }) {
  const [open, setOpen] = useState(step.status === "error");
  const hasDetails = Boolean(step.request || step.response);
  return (
    <li className="rounded-lg border border-border">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <span className="w-5 font-mono text-xs text-muted">{index + 1}</span>
        <span className="min-w-0 flex-1 text-sm text-foreground">
          {step.title}
        </span>
        {step.erid ? (
          <code className="rounded bg-panel px-1.5 py-0.5 font-mono text-xs text-accent">
            erid {step.erid}
          </code>
        ) : null}
        {step.http_status ? (
          <span className="font-mono text-xs text-muted">
            HTTP {step.http_status}
          </span>
        ) : null}
        {step.ms !== undefined ? (
          <span className="font-mono text-xs text-muted">{step.ms} мс</span>
        ) : null}
        <span
          className={`rounded-md px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[step.status]}`}
        >
          {STATUS_LABEL[step.status]}
        </span>
        {hasDetails ? (
          <Button
            variant="ghost"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
          >
            {open ? "Скрыть" : "Детали"}
          </Button>
        ) : null}
      </div>
      {step.error ? (
        <p className="px-3 pb-2 text-xs text-danger">{step.error}</p>
      ) : null}
      {open && hasDetails ? (
        <div className="grid gap-2 border-t border-border px-3 py-2 md:grid-cols-2">
          {step.request ? (
            <div className="min-w-0">
              <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.14em] text-muted">
                {step.request.method} {step.request.path}
              </p>
              <pre className="max-h-64 overflow-auto rounded bg-panel p-2 font-mono text-[11px] leading-relaxed text-foreground">
                {JSON.stringify(step.request.body, null, 2)}
              </pre>
            </div>
          ) : null}
          {step.response ? (
            <div className="min-w-0">
              <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.14em] text-muted">
                Ответ ОРД
              </p>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-panel p-2 font-mono text-[11px] leading-relaxed text-foreground">
                {step.response}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function KktuReport({ items }: { items: OrdKktuItem[] }) {
  const diff = useMemo(() => compareKktu(KKTU_TREE, items), [items]);
  const clean =
    !diff.missingLocally.length &&
    !diff.invalidLocally.length &&
    !diff.renamed.length;
  return (
    <section
      aria-label="Сверка ККТУ"
      className="flex flex-col gap-2 rounded-lg border border-border p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-foreground">
          Сверка ККТУ с ОРД (C29)
        </h3>
        <span
          className={`rounded-md px-2 py-0.5 text-xs font-medium ${clean ? STATUS_STYLE.ok : STATUS_STYLE.error}`}
        >
          {clean ? "Совпадает" : "Есть расхождения"}
        </span>
        <span className="text-xs text-muted">
          у нас {diff.localLeaves} листьев · в ОРД {diff.ordLeaves} действующих
        </span>
      </div>
      {diff.invalidLocally.length ? (
        <details open>
          <summary className="cursor-pointer text-xs font-medium text-danger">
            {`Наши коды, которые ОРД не примет: ${diff.invalidLocally.length}`}
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 text-xs text-foreground">
            {diff.invalidLocally.map((i) => (
              <li key={i.code}>
                <code className="font-mono">{i.code}</code> {i.name} —{" "}
                {i.reason === "absent"
                  ? "нет в ОРД"
                  : i.reason === "deleted"
                    ? "удалён"
                    : "нельзя выбирать"}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {diff.missingLocally.length ? (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-foreground">
            {`Коды ОРД, которых нет у нас: ${diff.missingLocally.length}`}
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 text-xs text-foreground">
            {diff.missingLocally.map((i) => (
              <li key={i.code}>
                <code className="font-mono">{i.code}</code> {i.name}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {diff.renamed.length ? (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-foreground">
            {`Отличаются названия: ${diff.renamed.length}`}
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 text-xs text-foreground">
            {diff.renamed.map((i) => (
              <li key={i.code}>
                <code className="font-mono">{i.code}</code> {i.local} → {i.ord}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {!clean ? (
        <p className="text-xs text-muted">
          Обновите <code className="font-mono">frontend/lib/kktu.json</code> по
          этому списку, затем повторите сверку.
        </p>
      ) : null}
    </section>
  );
}

export function OrdSandboxCard({ savedSandbox }: { savedSandbox: boolean }) {
  const [apiKey, setApiKey] = useState("");
  const [advInn, setAdvInn] = useState("");
  const [advName, setAdvName] = useState("");
  const [selfInn, setSelfInn] = useState("");
  const [selfName, setSelfName] = useState("");
  const [kktu, setKktu] = useState("");
  const [running, setRunning] = useState<"full" | "kktu" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<SandboxRun | null>(null);

  const kktuItems = run?.steps.find((s) => s.key === "kktu")?.kktu;

  async function start(mode: "full" | "kktu") {
    setRunning(mode);
    setError(null);
    try {
      const out = await api<SandboxRun>("/settings/ord/sandbox-run", {
        method: "POST",
        body: {
          api_key: apiKey.trim() || null,
          advertiser_inn: advInn.trim() || null,
          advertiser_name: advName.trim() || null,
          self_inn: selfInn.trim() || null,
          self_name: selfName.trim() || null,
          kktu: kktu || null,
          steps: mode === "kktu" ? ["ping", "kktu"] : null,
        },
      });
      setRun(out);
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Не удалось выполнить прогон",
      );
    } finally {
      setRunning(null);
    }
  }

  const canRun = apiKey.trim().length >= 8 || savedSandbox;

  return (
    <Card>
      <CardHeader
        title="Тест в песочнице ОРД"
        subtitle="Сквозной прогон цепочки на api-ord-sandbox.ozon.ru: организации, договор, креатив с erid, статистика, акт. Данные кабинета не меняются."
      />
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 md:grid-cols-2">
          <Field
            label="API-ключ песочницы"
            hint="Используется только для этого прогона и нигде не сохраняется. Продовый ключ песочница отклонит."
          >
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className={inputCls}
              placeholder={
                savedSandbox
                  ? "Пусто — взять сохранённый ключ песочницы"
                  : "Вставьте ключ песочницы"
              }
            />
          </Field>
          <Field
            label="ККТУ креатива"
            hint="По умолчанию 30.15.1. Выберите другой, чтобы проверить конкретную категорию."
          >
            <KktuSelect
              value={kktu || null}
              onChange={(code) => setKktu(code)}
            />
          </Field>
        </div>

        <details className="rounded-lg border border-border px-3 py-2">
          <summary className="cursor-pointer text-sm text-foreground">
            Тестовые реквизиты (по умолчанию — синтетические ИНН с верной
            контрольной цифрой)
          </summary>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <Field label="ИНН рекламодателя">
              <input
                value={advInn}
                onChange={(e) => setAdvInn(e.target.value)}
                inputMode="numeric"
                maxLength={10}
                className={inputCls}
                placeholder="10 цифр"
              />
            </Field>
            <Field label="Название рекламодателя">
              <input
                value={advName}
                onChange={(e) => setAdvName(e.target.value)}
                className={inputCls}
                placeholder="ООО «Тест РД»"
              />
            </Field>
            <Field label="ИНН ОРС (наш)">
              <input
                value={selfInn}
                onChange={(e) => setSelfInn(e.target.value)}
                inputMode="numeric"
                maxLength={10}
                className={inputCls}
                placeholder="10 цифр"
              />
            </Field>
            <Field label="Название ОРС">
              <input
                value={selfName}
                onChange={(e) => setSelfName(e.target.value)}
                className={inputCls}
                placeholder="ООО «Тест ОРС»"
              />
            </Field>
          </div>
        </details>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            onClick={() => start("full")}
            disabled={!canRun || running !== null}
          >
            {running === "full" ? "Идёт прогон…" : "Запустить полный прогон"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => start("kktu")}
            disabled={!canRun || running !== null}
          >
            {running === "kktu"
              ? "Загружаем справочник…"
              : "Только сверка ККТУ"}
          </Button>
          <HelpTip text="Каждый прогон создаёт в песочнице новые сущности с префиксом sbx-<id>, поэтому его можно повторять сколько угодно." />
          {error ? <p className="text-xs text-danger">{error}</p> : null}
        </div>

        {run ? (
          <div className="flex flex-col gap-3" aria-live="polite">
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
              <span>
                Прогон{" "}
                <code className="font-mono text-foreground">{run.run_id}</code>{" "}
                · период {run.period}
              </span>
              <span className="text-success">успешно {run.summary.ok}</span>
              <span className="text-danger">ошибок {run.summary.error}</span>
              <span>пропущено {run.summary.skipped}</span>
            </div>
            <ol className="flex flex-col gap-2">
              {run.steps.map((s, i) => (
                <StepRow key={s.key} step={s} index={i} />
              ))}
            </ol>
            <p className="text-xs text-muted">
              ИНН: РД {run.entities.advertiser.inn}, ОРС {run.entities.self.inn}{" "}
              · договор{" "}
              <code className="font-mono">{run.entities.contract}</code> ·
              креатив <code className="font-mono">{run.entities.creative}</code>
            </p>
            {kktuItems ? <KktuReport items={kktuItems} /> : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}
