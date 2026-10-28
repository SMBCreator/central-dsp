"use client"

import type { ReactNode } from "react"
import { assetUrl } from "@/lib/api"
import { Modal } from "@/components/ui"
import { CreativePreview } from "@/components/creative-preview"
import { PixelEditor, TrackingBlock, type PixelEditTarget } from "@/components/creative-tracking"
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
  pixelEdit,
}: {
  creative: PreviewCreative | null
  campaignName?: string | null
  onClose: () => void
  footer?: ReactNode
  /** Передаётся только ролям с правом править креативы: показывает «Изменить пиксели». */
  pixelEdit?: { admin: boolean; onSaved: (updated: PixelEditTarget) => void }
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
          <TrackingBlock creative={creative} />
          {pixelEdit && creative.id ? (
            <PixelEditor
              key={creative.id}
              creative={{ id: creative.id, status: creative.status || "", imp_tracker_url: creative.imp_tracker_url, click_tracker_url: creative.click_tracker_url }}
              admin={pixelEdit.admin}
              onSaved={pixelEdit.onSaved}
            />
          ) : null}
          {footer ? <div className="flex justify-end gap-2">{footer}</div> : null}
        </div>
      ) : null}
    </Modal>
  )
}
