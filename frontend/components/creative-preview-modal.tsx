"use client"

import type { ReactNode } from "react"
import { assetUrl } from "@/lib/api"
import { Modal } from "@/components/ui"
import { CreativePreview } from "@/components/creative-preview"
import { advertiserFromCreative, previewSubtitle, type PreviewCreative } from "@/lib/creative-badge"

/**
 * Окно просмотра креатива в полном размере (баннер до 970 px, видео с controls) с
 * интерактивной плашкой «Реклама ⓘ». Esc, подложка и крестик закрывают (Modal),
 * фокус возвращается на миниатюру-инициатор.
 */
export function CreativePreviewModal({
  creative,
  campaignName,
  onClose,
  footer,
}: {
  creative: PreviewCreative | null
  campaignName?: string | null
  onClose: () => void
  footer?: ReactNode
}) {
  return (
    <Modal open={!!creative} onClose={onClose} size="xl" title={creative?.name || "Креатив"} subtitle={creative ? previewSubtitle(creative, campaignName) : undefined}>
      {creative ? (
        <div className="space-y-4">
          <div className="overflow-hidden rounded-md bg-panel p-3">
            <div className="w-full min-w-0 [&>*]:mx-auto">
              <CreativePreview
                fit="contain"
                type={creative.type}
                assetUrl={assetUrl(creative.asset_url) || null}
                width={creative.width}
                height={creative.height}
                nativeTitle={creative.native_title}
                nativeDescription={creative.native_description}
                nativeCta={creative.native_cta}
                nativeIconUrl={assetUrl(creative.native_icon_url) || null}
                videoUrl={assetUrl(creative.video_url) || null}
                advertiser={advertiserFromCreative(creative)}
              />
            </div>
          </div>
          {footer ? <div className="flex justify-end gap-2">{footer}</div> : null}
        </div>
      ) : null}
    </Modal>
  )
}
