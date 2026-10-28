import { AD_TYPE_LABELS } from "@/lib/types"

/** Данные рекламодателя для плашки «Реклама ⓘ» (38-ФЗ). */
export interface AdvertiserInfo {
  name?: string | null
  inn?: string | null
  ogrn?: string | null
  address?: string | null
  erid?: string | null
}

export const ERID_PLACEHOLDER = "erid: будет присвоен после регистрации в ОРД"

/** Строки всплывашки плашки: erid (или заглушка) и непустые реквизиты. */
export function advertiserLines(a: AdvertiserInfo): string[] {
  return [
    a.erid ? `erid: ${a.erid}` : ERID_PLACEHOLDER,
    a.name ? `Рекламодатель: ${a.name}` : null,
    a.inn ? `ИНН: ${a.inn}` : null,
    a.ogrn ? `ОГРН: ${a.ogrn}` : null,
    a.address ? `Адрес: ${a.address}` : null,
  ].filter((l): l is string => !!l)
}

/** Поля креатива/элемента очереди модерации, из которых собирается превью. */
export interface PreviewCreative {
  id?: string
  status?: string
  click_url?: string | null
  imp_tracker_url?: string | null
  click_tracker_url?: string | null
  name: string
  type: "banner" | "native" | "video" | "push"
  width?: number | null
  height?: number | null
  asset_url?: string | null
  native_title?: string | null
  native_description?: string | null
  native_cta?: string | null
  native_icon_url?: string | null
  video_url?: string | null
  erid?: string | null
  counterparty_name?: string | null
  counterparty_inn?: string | null
  counterparty_ogrn?: string | null
  counterparty_address?: string | null
}

/** Реквизиты для плашки из строки списка креативов или очереди модерации. */
export function advertiserFromCreative(c: PreviewCreative): AdvertiserInfo {
  return {
    name: c.counterparty_name ?? null,
    inn: c.counterparty_inn ?? null,
    ogrn: c.counterparty_ogrn ?? null,
    address: c.counterparty_address ?? null,
    erid: c.erid ?? null,
  }
}

export const THUMB_W = 96
export const THUMB_H = 72

/** Вписывает width×height в рамку миниатюры с сохранением пропорций, без увеличения сверх натурального размера. */
export function thumbBox(
  width: number | null | undefined,
  height: number | null | undefined,
  maxW = THUMB_W,
  maxH = THUMB_H,
): { width: number; height: number } {
  if (!width || !height || width <= 0 || height <= 0) return { width: maxW, height: maxH }
  const k = Math.min(maxW / width, maxH / height, 1)
  return { width: Math.max(1, Math.round(width * k)), height: Math.max(1, Math.round(height * k)) }
}

/** «Тип · размер · кампания» — подзаголовок окна просмотра; пустые части пропускаются. */
export function previewSubtitle(c: PreviewCreative, campaignName?: string | null): string {
  const size = c.width && c.height ? `${c.width}×${c.height}` : null
  return [AD_TYPE_LABELS[c.type] || c.type, size, campaignName || null].filter(Boolean).join(" · ")
}

export function thumbAriaLabel(name: string): string {
  return `Открыть креатив «${name}» в полном размере`
}

export interface PopupPosition {
  top: number
  left: number
}

/**
 * Положение всплывашки (position: fixed) относительно кнопки: выравнивание по правому краю
 * кнопки, под ней; не выходит за экран — сдвигается влево/вверх при нехватке места.
 */
export function popupPosition(
  anchor: { top: number; bottom: number; right: number },
  popup: { width: number; height: number },
  viewport: { width: number; height: number },
  margin = 8,
): PopupPosition {
  let left = anchor.right - popup.width
  left = Math.min(left, viewport.width - popup.width - margin)
  left = Math.max(margin, left)
  let top = anchor.bottom + 4
  if (top + popup.height > viewport.height - margin) {
    const above = anchor.top - popup.height - 4
    top = above >= margin ? above : Math.max(margin, viewport.height - popup.height - margin)
  }
  return { top, left }
}
