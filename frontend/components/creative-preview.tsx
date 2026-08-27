"use client"

/**
 * Превью креатива ровно в том виде, в каком его увидит пользователь на сайте:
 * с обязательным инфо-значком «Реклама» (38-ФЗ) в углу, по клику раскрывается
 * информация о рекламодателе (erid, наименование, ИНН, ОГРН, адрес).
 * Разметка повторяет ту, что Go-биддер вшивает в adm.
 */

import { useState } from "react"

export interface AdvertiserInfo {
  name?: string | null
  inn?: string | null
  ogrn?: string | null
  address?: string | null
  erid?: string | null
}

interface CreativePreviewProps {
  type: "banner" | "native" | "video"
  assetUrl?: string | null
  width?: number | null
  height?: number | null
  nativeTitle?: string | null
  nativeDescription?: string | null
  nativeCta?: string | null
  nativeIconUrl?: string | null
  videoUrl?: string | null
  advertiser: AdvertiserInfo
}

function InfoBadge({ advertiser }: { advertiser: AdvertiserInfo }) {
  const [open, setOpen] = useState(false)
  const lines = [
    advertiser.erid ? `erid: ${advertiser.erid}` : "erid: будет присвоен после регистрации в ОРД",
    advertiser.name ? `Рекламодатель: ${advertiser.name}` : null,
    advertiser.inn ? `ИНН: ${advertiser.inn}` : null,
    advertiser.ogrn ? `ОГРН: ${advertiser.ogrn}` : null,
    advertiser.address ? `Адрес: ${advertiser.address}` : null,
  ].filter(Boolean) as string[]

  return (
    <div className="absolute right-0 top-0 z-10">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        aria-expanded={open}
        aria-label="Информация о рекламодателе"
        className="flex items-center gap-1 rounded-bl bg-black/60 px-1.5 py-0.5 text-[10px] font-medium leading-none text-white backdrop-blur-sm"
      >
        Реклама
        <span className="flex h-3 w-3 items-center justify-center rounded-full border border-white/70 text-[8px] leading-none">i</span>
      </button>
      {open ? (
        <div className="absolute right-0 top-5 w-56 rounded border border-black/10 bg-white p-2 text-left shadow-lg">
          {lines.map((l) => (
            <p key={l} className="text-[10px] leading-relaxed text-neutral-800">
              {l}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export function CreativePreview({ type, assetUrl, width, height, nativeTitle, nativeDescription, nativeCta, nativeIconUrl, videoUrl, advertiser }: CreativePreviewProps) {
  if (type === "banner") {
    const w = width || 300
    const h = height || 250
    const scale = w > 400 ? 400 / w : 1
    return (
      <div className="relative inline-block overflow-hidden rounded border border-border bg-white" style={{ width: w * scale, height: h * scale }}>
        {assetUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={assetUrl || "/placeholder.svg"} alt={`Баннер ${w}×${h}`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-surface text-xs text-muted">{`${w}×${h}`}</div>
        )}
        <InfoBadge advertiser={advertiser} />
      </div>
    )
  }

  if (type === "native") {
    return (
      <div className="relative w-72 overflow-hidden rounded border border-border bg-white p-3">
        <InfoBadge advertiser={advertiser} />
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

  return (
    <div className="relative w-96 overflow-hidden rounded border border-border bg-black">
      {videoUrl ? (
        <video src={videoUrl} controls muted className="h-54 w-full" aria-label="Превью видеообъявления" />
      ) : (
        <div className="flex h-54 w-full items-center justify-center text-xs text-white/60">Видео не загружено</div>
      )}
      <InfoBadge advertiser={advertiser} />
    </div>
  )
}
