"use client"

import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { useMe } from "@/lib/use-me"
import { fmtNum } from "@/lib/types"
import { Card, CardHeader, HelpTip, Spinner } from "@/components/ui"

type MlStatus = {
  pctr_model: {
    version: number
    trained_at: string | null
    window_from: string | null
    window_to: string | null
    sample_events: number
    metrics: { auc_like?: number; segments?: number; global_ctr?: number }
    params: Record<string, unknown>
  } | null
  domain_quality: {
    domains_scored: number
    flagged: number
    last_update: string | null
  }
  clean_traffic_campaigns: number
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
}

/** Статус ML-слоя (Часть B). Виден только admin/superadmin — как и сам API. */
export function MlStatusCard() {
  const { me } = useMe()
  const isAdmin = me?.role === "admin" || me?.role === "superadmin"
  const { data, isLoading } = useSWR<MlStatus>(isAdmin ? "/settings/ml/status" : null, fetcher)

  if (!isAdmin) return null

  const model = data?.pctr_model
  const dq = data?.domain_quality

  return (
    <Card>
      <CardHeader
        title="ML-слой закупки"
        subtitle="pCTR-модель для value-based биддинга и скоринг качества доменов"
        action={
          <HelpTip text="Модель переобучается кроном /cron/ml-train, скоринг доменов — /cron/domain-quality. Если модель недоступна, биддер работает по базовой ставке (fail-safe)." />
        }
      />
      {isLoading ? (
        <div className="p-8">
          <Spinner />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-3">
          <div className="rounded-2xl bg-panel p-4">
            <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">pCTR-модель</p>
            {model ? (
              <>
                <p className="mt-1 text-lg font-semibold text-foreground">v{model.version}</p>
                <p className="mt-0.5 text-xs text-muted">
                  Обучена {fmtDate(model.trained_at)} · {fmtNum(model.sample_events)} событий
                  {model.metrics?.segments != null ? ` · ${fmtNum(model.metrics.segments)} сегментов` : ""}
                </p>
              </>
            ) : (
              <>
                <p className="mt-1 text-lg font-semibold text-muted">Нет активной</p>
                <p className="mt-0.5 text-xs text-muted">Биддинг идёт по базовой ставке. Запустите /cron/ml-train после накопления кликов.</p>
              </>
            )}
          </div>
          <div className="rounded-2xl bg-panel p-4">
            <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">Качество доменов</p>
            <p className="mt-1 text-lg font-semibold text-foreground">{fmtNum(dq?.domains_scored ?? 0)}</p>
            <p className="mt-0.5 text-xs text-muted">
              доменов со скорингом · {fmtNum(dq?.flagged ?? 0)} ниже порога · обновлено {fmtDate(dq?.last_update ?? null)}
            </p>
          </div>
          <div className="rounded-2xl bg-panel p-4">
            <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">Чистый трафик</p>
            <p className="mt-1 text-lg font-semibold text-foreground">{fmtNum(data?.clean_traffic_campaigns ?? 0)}</p>
            <p className="mt-0.5 text-xs text-muted">активных кампаний с режимом «Только чистый трафик»</p>
          </div>
        </div>
      )}
    </Card>
  )
}
