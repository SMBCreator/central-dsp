"use client"

/**
 * Превью креатива ровно в том виде, в каком его увидит пользователь на сайте:
 * с обязательным инфо-значком «Реклама» (38-ФЗ) в углу, по клику раскрывается
 * информация о рекламодателе (erid, наименование, ИНН, ОГРН, адрес).
 * Разметка повторяет ту, что Go-биддер вшивает в adm.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { advertiserLines, popupPosition, type AdvertiserInfo, type PopupPosition } from "@/lib/creative-badge"

export type { AdvertiserInfo }

interface CreativePreviewProps {
  type: "banner" | "native" | "video" | "push"
  assetUrl?: string | null
  width?: number | null
  height?: number | null
  nativeTitle?: string | null
  nativeDescription?: string | null
  nativeCta?: string | null
  nativeIconUrl?: string | null
  videoUrl?: string | null
  advertiser: AdvertiserInfo
  /** natural — как раньше (баннер ≤400 px); contain — вписывается в ширину контейнера. */
  fit?: "natural" | "contain"
}

const POPUP_WIDTH = 224

function BadgeLabel({ compact }: { compact?: boolean }) {
  return (
    <>
      Реклама
      <span
        className={`flex items-center justify-center rounded-full border border-white/70 leading-none ${
          compact ? "h-2 w-2 text-[6px]" : "h-3 w-3 text-[8px]"
        }`}
      >
        i
      </span>
    </>
  )
}

/**
 * Плашка «Реклама ⓘ». interactive=false — только метка без кнопки (внутри миниатюр-кнопок);
 * иначе по клику открывается всплывашка с реквизитами через портал в body (не обрезается
 * overflow-hidden баннера). Закрытие: клик вне, Esc, повторный клик.
 */
export function AdInfoBadge({
  advertiser,
  compact = false,
  interactive = true,
}: {
  advertiser: AdvertiserInfo
  compact?: boolean
  interactive?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<PopupPosition | null>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)

  const place = useCallback(() => {
    const btn = buttonRef.current
    if (!btn) return
    const r = btn.getBoundingClientRect()
    const h = popupRef.current?.offsetHeight ?? 120
    setPos(popupPosition(r, { width: POPUP_WIDTH, height: h }, { width: window.innerWidth, height: window.innerHeight }))
  }, [])

  useLayoutEffect(() => {
    if (open) place()
  }, [open, place, advertiser])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (buttonRef.current?.contains(t) || popupRef.current?.contains(t)) return
      setOpen(false)
    }
    // Capture на window: Esc закрывает сначала всплывашку, а не окно просмотра под ней.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      e.stopPropagation()
      setOpen(false)
      buttonRef.current?.focus()
    }
    document.addEventListener("mousedown", onDown)
    window.addEventListener("keydown", onKey, true)
    window.addEventListener("resize", place)
    window.addEventListener("scroll", place, true)
    return () => {
      document.removeEventListener("mousedown", onDown)
      window.removeEventListener("keydown", onKey, true)
      window.removeEventListener("resize", place)
      window.removeEventListener("scroll", place, true)
    }
  }, [open, place])

  const sizeCls = compact ? "gap-0.5 px-1 py-px text-[8px]" : "gap-1 px-1.5 py-0.5 text-[10px]"

  if (!interactive) {
    return (
      <div className="pointer-events-none absolute right-0 top-0 z-10">
        <span className={`flex items-center rounded-bl bg-black/60 font-medium leading-none text-white ${sizeCls}`}>
          <BadgeLabel compact={compact} />
        </span>
      </div>
    )
  }

  return (
    <div className="absolute right-0 top-0 z-10">
      <button
        ref={buttonRef}
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="Информация о рекламодателе"
        className={`flex items-center rounded-bl bg-black/60 font-medium leading-none text-white backdrop-blur-sm ${sizeCls}`}
      >
        <BadgeLabel compact={compact} />
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={popupRef}
              role="dialog"
              aria-label="Информация о рекламодателе"
              style={{ position: "fixed", top: pos?.top ?? 0, left: pos?.left ?? 0, width: POPUP_WIDTH, visibility: pos ? "visible" : "hidden" }}
              className="z-[70] rounded border border-black/10 bg-white p-2 text-left shadow-lg"
            >
              {advertiserLines(advertiser).map((l) => (
                <p key={l} className="break-words text-[11px] leading-relaxed text-neutral-800">
                  {l}
                </p>
              ))}
            </div>,
            document.body,
          )
        : null}
    </div>
  )
}

export function CreativePreview({ type, assetUrl, width, height, nativeTitle, nativeDescription, nativeCta, nativeIconUrl, videoUrl, advertiser, fit = "natural" }: CreativePreviewProps) {
  const contain = fit === "contain"
  if (type === "banner") {
    const w = width || 300
    const h = height || 250
    const scale = !contain && w > 400 ? 400 / w : 1
    const boxStyle = contain
      ? { width: w, maxWidth: "100%", aspectRatio: `${w} / ${h}` }
      : { width: w * scale, height: h * scale }
    return (
      <div className={`relative ${contain ? "block" : "inline-block"} overflow-hidden rounded border border-border bg-white`} style={boxStyle}>
        {assetUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={assetUrl || "/placeholder.svg"} alt={`Баннер ${w}×${h}`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-surface text-xs text-muted">{`${w}×${h}`}</div>
        )}
        <AdInfoBadge advertiser={advertiser} />
      </div>
    )
  }

  if (type === "native") {
    return (
      <div className="relative w-72 max-w-full overflow-hidden rounded border border-border bg-white p-3">
        <AdInfoBadge advertiser={advertiser} />
        <div className="flex gap-2.5">
          {nativeIconUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={nativeIconUrl || "/placeholder.svg"} alt="" className="h-10 w-10 shrink-0 rounded object-cover" />
          ) : null}
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-neutral-900">{nativeTitle || "Заголовок объявления"}</p>
            {nativeDescription ? <p className="mt-0.5 line-clamp-2 text-xs text-neutral-600">{nativeDescription}</p> : null}
          </div>
        </div>
        {assetUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={assetUrl || "/placeholder.svg"} alt="" className="mt-2 max-h-36 w-full rounded object-cover" />
        ) : null}
        <span className="mt-2 inline-block rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white">{nativeCta || "Подробнее"}</span>
      </div>
    )
  }

  if (type === "push") {
    // Имит��ция системного push-уведомления: иконка + заголовок + текст,
    // ниже — большая картинка (big image), как в push-сетях.
    return (
      <div className="relative w-80 max-w-full overflow-hidden rounded-lg border border-border bg-white shadow-sm">
        <AdInfoBadge advertiser={advertiser} />
        <div className="flex items-start gap-2.5 p-3">
          {nativeIconUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={nativeIconUrl || "/placeholder.svg"} alt="" className="h-10 w-10 shrink-0 rounded-lg object-cover" />
          ) : (
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-surface text-[10px] text-muted">icon</div>
          )}
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-neutral-900">{nativeTitle || "Заголовок push-уведомления"}</p>
            {nativeDescription ? <p className="mt-0.5 line-clamp-2 text-xs text-neutral-600">{nativeDescription}</p> : null}
            <p className="mt-1 text-[10px] text-neutral-400">Сейчас · push-уведомление</p>
          </div>
        </div>
        {assetUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={assetUrl || "/placeholder.svg"} alt="" className="max-h-40 w-full object-cover" />
        ) : null}
      </div>
    )
  }

  // Видео: контейнер не шире родителя (в модалке колонка превью 22rem, раньше
  // фиксированные w-96 обрезали бейдж «Реклама» справа), аспект 16:9 как у плеера.
  return (
    <div className={`relative aspect-video w-full overflow-hidden ${contain ? "max-w-3xl" : "max-w-96"} rounded border border-border bg-black`}>
      {videoUrl ? (
        <video src={videoUrl} controls muted className="h-full w-full object-contain" aria-label="Превью видеообъявления" />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-xs text-white/60">Видео не загружено</div>
      )}
      <AdInfoBadge advertiser={advertiser} />
    </div>
  )
}
