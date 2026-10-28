"use client"

import { assetUrl } from "@/lib/api"
import { AdInfoBadge } from "@/components/creative-preview"
import { THUMB_H, THUMB_W, advertiserFromCreative, thumbAriaLabel, thumbBox, type PreviewCreative } from "@/lib/creative-badge"

/**
 * Миниатюра креатива: кнопка ≈96×72 px, содержимое вписано с сохранением пропорций.
 * Плашка «Реклама ⓘ» внутри — без кнопки (interactive=false), чтобы не вкладывать кнопку в кнопку.
 */
export function CreativeThumb({ creative, onOpen }: { creative: PreviewCreative; onOpen: () => void }) {
  const advertiser = advertiserFromCreative(creative)
  const src = assetUrl(creative.asset_url) || ""
  const frame = "relative flex shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-panel"

  let body: React.ReactNode
  if (creative.type === "banner") {
    const box = thumbBox(creative.width, creative.height)
    body = src ? (
      <span className="relative inline-block overflow-hidden rounded-sm" style={{ width: box.width, height: box.height }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src || "/placeholder.svg"} alt="" className="h-full w-full object-cover" />
        <AdInfoBadge advertiser={advertiser} compact interactive={false} />
      </span>
    ) : (
      <>
        <span className="font-mono text-[10px] text-muted">
          {creative.width && creative.height ? `${creative.width}×${creative.height}` : "баннер"}
        </span>
        <AdInfoBadge advertiser={advertiser} compact interactive={false} />
      </>
    )
  } else if (creative.type === "video") {
    const video = assetUrl(creative.video_url) || ""
    body = (
      <>
        {video ? (
          <video src={`${video}#t=0.1`} preload="metadata" muted playsInline tabIndex={-1} aria-hidden="true" className="h-full w-full object-cover" />
        ) : (
          <span className="text-[10px] text-muted">Видео</span>
        )}
        <AdInfoBadge advertiser={advertiser} compact interactive={false} />
      </>
    )
  } else {
    const icon = assetUrl(creative.native_icon_url) || src
    body = (
      <>
        <span className="flex w-full items-center gap-1 px-1.5 pt-3">
          {icon ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={icon || "/placeholder.svg"} alt="" className="h-6 w-6 shrink-0 rounded object-cover" />
          ) : (
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-card text-[8px] text-muted">
              {creative.type === "push" ? "push" : "ad"}
            </span>
          )}
          <span className="line-clamp-3 min-w-0 text-left text-[9px] font-medium leading-tight text-foreground">
            {creative.native_title || (creative.type === "push" ? "Push" : "Нативное")}
          </span>
        </span>
        <AdInfoBadge advertiser={advertiser} compact interactive={false} />
      </>
    )
  }

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={thumbAriaLabel(creative.name)}
      title="Открыть в полном размере"
      className={`${frame} transition-colors hover:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`}
      style={{ width: THUMB_W, height: THUMB_H }}
    >
      {body}
    </button>
  )
}
