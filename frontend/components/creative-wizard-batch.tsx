"use client"

import { useEffect, useRef, useState } from "react"
import { apiDeleteUpload } from "@/lib/api"
import { useAdSizes } from "@/lib/ad-sizes"
import { Button, Field, inputCls } from "@/components/ui"
import { CreativeBatchPanel, useCreativeBatch } from "@/components/creative-batch-panel"
import type { AdvertiserInfo } from "@/lib/creative-badge"
import type { CreativeDraft } from "@/components/creative-builder"
import type { BatchResultItem } from "@/lib/creative-batch"
import { itemToBannerDraft, planWizardBatch } from "@/lib/creative-wizard"

/**
 * Мастер «Новая кампания», шаг «Креативы», формат «Баннер»: пакетная загрузка файлов
 * (кнопки «Выбрать файлы/папку», перетаскивание, карточка на файл с предпросмотром и
 * автоопределением размера). Готовые файлы по кнопке «Добавить в список» становятся
 * черновиками креативов; сами креативы мастер создаёт при запуске кампании.
 */
export function WizardBannerBatch({
  creatives,
  onChange,
  advertiser,
}: {
  creatives: CreativeDraft[]
  onChange: (list: CreativeDraft[]) => void
  advertiser?: AdvertiserInfo
}) {
  const adSizes = useAdSizes()
  const orphanPaths = useRef<Set<string>>(new Set())
  const batch = useCreativeBatch(adSizes, orphanPaths)
  const [clickUrl, setClickUrl] = useState<string>(() => {
    const last = [...creatives].reverse().find((c) => c.type === "banner" && !!c.click_url)
    return last ? last.click_url : ""
  })
  const { items, applyResults } = batch
  const plan = planWizardBatch(items, clickUrl)
  const busy = items.some((i) => i.phase === "queued" || i.phase === "uploading")

  // Уход с шага: загруженные, но не добавленные в список файлы удаляем с сервера.
  useEffect(() => {
    const orphans = orphanPaths
    return () => {
      for (const p of orphans.current) void apiDeleteUpload(p)
      orphans.current = new Set()
    }
  }, [])

  const addToList = () => {
    if (plan.ready.length === 0) return
    const drafts = plan.ready.map((i) => itemToBannerDraft(i, clickUrl))
    onChange([...creatives, ...drafts])
    // Файлы теперь принадлежат черновикам: убираем их из пакета, не удаляя с сервера.
    const results: BatchResultItem[] = plan.ready.map((i) => ({ client_id: i.id, status: "created" }))
    applyResults(results)
  }

  return (
    <div className="space-y-3">
      <Field
        label="Ссылка перехода по умолчанию"
        hint="Для всех загружаемых файлов. У каждого файла в его карточке можно указать свою ссылку."
      >
        <input
          type="url"
          value={clickUrl}
          onChange={(e) => setClickUrl(e.target.value)}
          className={inputCls}
          placeholder="https://site.ru/offer"
        />
      </Field>

      <CreativeBatchPanel batch={batch} defaultClickUrl={clickUrl.trim()} advertiser={advertiser} />

      {plan.needLink.length > 0 ? (
        <p role="status" className="text-xs text-warning">
          Файлов без ссылки перехода: {plan.needLink.length}. Укажите ссылку https://… выше или в карточке файла.
        </p>
      ) : null}

      {items.length > 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 py-2">
          <p className="text-xs text-muted" aria-live="polite">
            {busy ? "Идёт загрузка файлов…" : `Готово к добавлению: ${plan.ready.length}`}
          </p>
          <Button onClick={addToList} disabled={plan.ready.length === 0}>
            {`Добавить в список: ${plan.ready.length}`}
          </Button>
        </div>
      ) : null}
    </div>
  )
}
