/**
 * Мастер «Новая кампания», шаг «Креативы»: превращение загруженных пакетом файлов
 * в черновики баннеров. Чистая логика без DOM (тесты — tests/creative-wizard.test.ts).
 */
import type { CreativeDraft } from "@/components/creative-builder"
import { baseName, isHttpUrl, isSubmittable, type BatchItem } from "@/lib/creative-batch"

/** Ссылка перехода файла: своя, а если не задана — общая. */
export function effectiveClickUrl(item: BatchItem, defaultClickUrl: string): string {
  return item.clickUrl.trim() || defaultClickUrl.trim()
}

/** Название креатива: из карточки, а если очищено — имя файла без расширения. */
export function effectiveName(item: BatchItem): string {
  return item.name.trim() || baseName(item.fileName)
}

/** Загруженный и проверенный файл -> черновик баннера (создаётся при запуске кампании). */
export function itemToBannerDraft(item: BatchItem, defaultClickUrl: string): CreativeDraft {
  return {
    name: effectiveName(item),
    type: "banner",
    subtype: item.mime === "image/gif" ? "gif" : "static",
    width: item.width,
    height: item.height,
    asset_url: item.assetUrl,
    click_url: effectiveClickUrl(item, defaultClickUrl),
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
}

export function planWizardBatch(items: BatchItem[], defaultClickUrl: string): WizardBatchPlan {
  const uploaded = items.filter(isSubmittable)
  return {
    ready: uploaded.filter((i) => isHttpUrl(effectiveClickUrl(i, defaultClickUrl))),
    needLink: uploaded.filter((i) => !isHttpUrl(effectiveClickUrl(i, defaultClickUrl))),
  }
}
