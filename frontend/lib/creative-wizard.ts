/**
 * Мастер «Новая кампания», шаг «Креативы»: превращение загруженных пакетом файлов
 * в черновики баннеров. Чистая логика без DOM (тесты — tests/creative-wizard.test.ts).
 */
import type { CreativeDraft } from "@/components/creative-builder"
import {
  NO_TRACKERS,
  baseName,
  effectiveTracker,
  isHttpUrl,
  isSubmittable,
  isValidPixel,
  type BatchItem,
  type BatchTrackers,
} from "@/lib/creative-batch"

/** Ссылка перехода файла: своя, а если не задана — общая. */
export function effectiveClickUrl(item: BatchItem, defaultClickUrl: string): string {
  return item.clickUrl.trim() || defaultClickUrl.trim()
}

/** Название креатива: из карточки, а если очищено — имя файла без расширения. */
export function effectiveName(item: BatchItem): string {
  return item.name.trim() || baseName(item.fileName)
}

/** Загруженный и проверенный файл -> черновик баннера (создаётся при запуске кампании). */
export function itemToBannerDraft(item: BatchItem, defaultClickUrl: string, trackers: BatchTrackers = NO_TRACKERS): CreativeDraft {
  return {
    name: effectiveName(item),
    type: "banner",
    subtype: item.mime === "image/gif" ? "gif" : "static",
    width: item.width,
    height: item.height,
    asset_url: item.assetUrl,
    click_url: effectiveClickUrl(item, defaultClickUrl),
    imp_tracker_url: effectiveTracker(item.impTrackerUrl, trackers.imp),
    click_tracker_url: effectiveTracker(item.clickTrackerUrl, trackers.click),
    html_snippet: null,
    native_title: null,
    native_description: null,
    native_cta: null,
    native_icon_url: null,
    video_url: null,
    video_duration: null,
    vast_xml: null,
    crop: null,
  }
}

export interface WizardBatchPlan {
  /** Можно добавлять: загружены, без ошибок, есть корректная ссылка перехода. */
  ready: BatchItem[]
  /** Загружены без ошибок, но ссылка перехода не задана или некорректна. */
  needLink: BatchItem[]
  /** Загружены, но итоговый пиксель показа или клика не является http(s)-адресом. */
  badPixel: BatchItem[]
}

function pixelsOk(i: BatchItem, t: BatchTrackers): boolean {
  return isValidPixel(effectiveTracker(i.impTrackerUrl, t.imp) ?? "") && isValidPixel(effectiveTracker(i.clickTrackerUrl, t.click) ?? "")
}

export function planWizardBatch(items: BatchItem[], defaultClickUrl: string, trackers: BatchTrackers = NO_TRACKERS): WizardBatchPlan {
  const uploaded = items.filter(isSubmittable)
  const linked = uploaded.filter((i) => isHttpUrl(effectiveClickUrl(i, defaultClickUrl)))
  return {
    ready: linked.filter((i) => pixelsOk(i, trackers)),
    needLink: uploaded.filter((i) => !isHttpUrl(effectiveClickUrl(i, defaultClickUrl))),
    badPixel: linked.filter((i) => !pixelsOk(i, trackers)),
  }
}

export interface LaunchContext {
  campaignId: string
  lineItemId: string
  foreign: boolean
  counterpartyId?: string | null
  kktuCode?: string | null
  kktuLabel?: string | null
}

/** Тело POST /creatives для черновика при запуске кампании: поля черновика (включая пиксели) + привязки. */
export function creativeLaunchBody(cr: CreativeDraft, ctx: LaunchContext): Record<string, unknown> {
  return {
    ...cr,
    campaign_id: ctx.campaignId,
    line_item_id: ctx.lineItemId,
    ...(ctx.foreign ? {} : { counterparty_id: ctx.counterpartyId, kktu_code: ctx.kktuCode || null, kktu_label: ctx.kktuLabel || null }),
  }
}
