"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, ApiError, fetcher } from "@/lib/api";
import type { PartnerIdentity, SupplyPartner, SyncUrls } from "@/lib/types";
import { Button, Field, HelpTip, Spinner, inputCls } from "@/components/ui";

const DEFAULTS: PartnerIdentity = {
  sync_enabled: false,
  sync_in_param: "ssp_uid",
  sync_callback_url_template: null,
  sync_redirect_hosts: [],
  id_source: "user_id",
  ttl_days: 90,
  requires_consent: true,
};

type FormState = Omit<
  PartnerIdentity,
  "sync_redirect_hosts" | "ttl_days" | "sync_callback_url_template"
> & {
  hosts: string;
  ttl_days: string;
  callback: string;
};

function toForm(i: PartnerIdentity | null | undefined): FormState {
  const v = { ...DEFAULTS, ...(i || {}) };
  return {
    sync_enabled: v.sync_enabled,
    sync_in_param: v.sync_in_param,
    id_source: v.id_source,
    requires_consent: v.requires_consent,
    hosts: v.sync_redirect_hosts.join("\n"),
    ttl_days: String(v.ttl_days),
    callback: v.sync_callback_url_template || "",
  };
}

function CopyRow({ label, value }: { label: string; value: string | null }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
        {label}
      </span>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded bg-panel px-2 py-1 font-mono text-xs text-foreground">
          {value || "—"}
        </code>
        {value ? (
          <Button
            variant="ghost"
            onClick={() => {
              navigator.clipboard.writeText(value).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              });
            }}
          >
            {copied ? "Скопировано" : "Копировать"}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** ID sync партнёра (docs/ID_SYNC.md): конфиг + URL для анкеты SSP. */
export function SspIdSync({ partner }: { partner: SupplyPartner }) {
  const [form, setForm] = useState<FormState>(() => toForm(partner.identity));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const urlsKey = `/supply-partners/${partner.id}/sync-urls`;
  const { data: urls, isLoading } = useSWR<SyncUrls>(urlsKey, fetcher);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api(`/supply-partners/${partner.id}`, {
        method: "PATCH",
        body: {
          identity: {
            sync_enabled: form.sync_enabled,
            sync_in_param: form.sync_in_param.trim(),
            sync_callback_url_template: form.callback.trim() || null,
            sync_redirect_hosts: form.hosts
              .split(/[\n,;\s]+/)
              .map((s) => s.trim())
              .filter(Boolean),
            id_source: form.id_source.trim(),
            ttl_days: Number(form.ttl_days),
            requires_consent: form.requires_consent,
          },
        },
      });
      setSaved(true);
      await Promise.all([mutate("/supply-partners"), mutate(urlsKey)]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section
      aria-label="ID sync"
      className="mt-4 flex flex-col gap-4 border-t border-border pt-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-foreground">
          Синхронизация ID (cookie matching)
        </h3>
        <HelpTip text="Связывает ID пользователя у SSP с нашим dsp_uid: аудитории и частота работают между SSP. Включается глобально флагом ID_SYNC_ENABLED и для каждого партнёра отдельно." />
        {urls && !urls.enabled_globally ? (
          <span className="rounded-md bg-muted/10 px-2 py-0.5 text-xs text-muted">
            ID_SYNC_ENABLED выключен
          </span>
        ) : null}
      </div>

      <form onSubmit={save} className="grid gap-3 md:grid-cols-2">
        <label className="flex items-center gap-2 text-sm text-foreground md:col-span-2">
          <input
            type="checkbox"
            checked={form.sync_enabled}
            onChange={(e) =>
              setForm({ ...form, sync_enabled: e.target.checked })
            }
            className="h-4 w-4 rounded border-border accent-accent"
          />
          Включить sync для этого SSP
        </label>
        <Field
          label="Источник ID в bid request"
          hint="Где SSP передаёт свой ID пользователя: user.id или eids с нужным source. buyeruid/ifa в sync не участвуют."
        >
          <select
            value={form.id_source.startsWith("eids:") ? "eids" : form.id_source}
            onChange={(e) =>
              setForm({
                ...form,
                id_source: e.target.value === "eids" ? "eids:" : e.target.value,
              })
            }
            className={inputCls}
          >
            <option value="user_id">user.id</option>
            <option value="eids">user.eids (source)</option>
            <option value="buyeruid">buyeruid (sync не нужен)</option>
            <option value="ifa">device.ifa (sync не нужен)</option>
          </select>
        </Field>
        {form.id_source.startsWith("eids:") ? (
          <Field
            label="eids source"
            hint="Значение eids[].source, например ssp.example."
          >
            <input
              value={form.id_source.slice(5)}
              onChange={(e) =>
                setForm({ ...form, id_source: `eids:${e.target.value.trim()}` })
              }
              className={inputCls}
              placeholder="ssp.example"
            />
          </Field>
        ) : (
          <Field
            label="Параметр с ID в пикселе /in"
            hint="Имя query-параметра, в котором SSP подставит свой ID."
          >
            <input
              value={form.sync_in_param}
              onChange={(e) =>
                setForm({ ...form, sync_in_param: e.target.value })
              }
              className={inputCls}
            />
          </Field>
        )}
        <Field
          label="Callback URL SSP"
          hint="Куда вернуть пользователя с нашим ID. Обязателен макрос {dsp_uid}, только https, домен — из списка ниже."
        >
          <input
            value={form.callback}
            onChange={(e) => setForm({ ...form, callback: e.target.value })}
            className={inputCls}
            placeholder="https://sync.ssp.example/match?dsp=centraldsp&uid={dsp_uid}"
          />
        </Field>
        <Field
          label="Разрешённые домены редиректа"
          hint="Один домен на строку, без https:// и пути. Защита от open redirect."
        >
          <textarea
            value={form.hosts}
            onChange={(e) => setForm({ ...form, hosts: e.target.value })}
            rows={2}
            className={inputCls}
            placeholder="sync.ssp.example"
          />
        </Field>
        <Field
          label="Срок жизни соответствия, дней"
          hint="1–395. После истечения соответствие удаляется джобой id_sync_purge."
        >
          <input
            type="number"
            min={1}
            max={395}
            value={form.ttl_days}
            onChange={(e) => setForm({ ...form, ttl_days: e.target.value })}
            className={inputCls}
          />
        </Field>
        <label className="flex items-center gap-2 self-end py-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={form.requires_consent}
            onChange={(e) =>
              setForm({ ...form, requires_consent: e.target.checked })
            }
            className="h-4 w-4 rounded border-border accent-accent"
          />
          Требовать строку согласия (gdpr_consent)
        </label>
        <div className="flex items-center justify-end gap-3 md:col-span-2">
          {error ? <p className="text-xs text-danger">{error}</p> : null}
          {saved ? <p className="text-xs text-muted">Сохранено</p> : null}
          <Button type="submit" disabled={saving}>
            {saving ? "Сохраняем…" : "Сохранить ID sync"}
          </Button>
        </div>
      </form>

      <div className="flex flex-col gap-3 rounded-lg bg-panel/50 p-3">
        <p className="text-xs text-muted">
          Отправьте SSP URL пикселя: макросы в фигурных скобках SSP заменяет
          своими значениями.
        </p>
        {isLoading ? (
          <Spinner />
        ) : urls ? (
          <>
            <CopyRow label="Пиксель SSP → DSP (/in)" value={urls.in_url} />
            <CopyRow
              label="Ссылка DSP → SSP (/out, одноразовая, 10 мин)"
              value={urls.out_url}
            />
            <CopyRow
              label="Отказ пользователя (opt-out)"
              value={urls.optout_url}
            />
            <p className="text-xs text-muted">
              Активных соответствий:{" "}
              <span className="font-medium text-foreground">
                {urls.active_mappings}
              </span>
              {!urls.secret_configured
                ? " · ID_SYNC_SECRET не задан — ссылка /out не выпускается"
                : ""}
            </p>
          </>
        ) : null}
      </div>
    </section>
  );
}
