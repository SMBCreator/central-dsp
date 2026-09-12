"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui"

/**
 * Canvas image cropper: load a file or URL, drag the crop window
 * (fixed aspect ratio), export an optimized JPEG data URL at target size.
 * Used for push / native creatives where SSP specs demand exact ratios.
 */
export function ImageCropper({
  aspect,
  outWidth,
  outHeight,
  label,
  onCropped,
}: {
  aspect: number // w/h
  outWidth: number
  outHeight: number
  label: string
  onCropped: (dataUrl: string, crop: { x: number; y: number; w: number; h: number }) => void
}) {
  const [img, setImg] = useState<HTMLImageElement | null>(null)
  const [url, setUrl] = useState("")
  const [error, setError] = useState<string | null>(null)
  // crop rect in relative coords (0..1) over the source image
  const [crop, setCrop] = useState({ x: 0, y: 0, w: 1, h: 1 })
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const dragRef = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null)

  const loadImage = useCallback((src: string) => {
    setError(null)
    const image = new Image()
    image.crossOrigin = "anonymous"
    image.onload = () => {
      // initial crop: max area with the required aspect, centered
      const imgAspect = image.naturalWidth / image.naturalHeight
      let w = 1
      let h = 1
      if (imgAspect > aspect) w = (aspect * image.naturalHeight) / image.naturalWidth
      else h = image.naturalWidth / aspect / image.naturalHeight
      setCrop({ x: (1 - w) / 2, y: (1 - h) / 2, w, h })
      setImg(image)
    }
    image.onerror = () => setError("Не удалось загрузить изображение (проверьте URL/CORS)")
    image.src = src
  }, [aspect])

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    const reader = new FileReader()
    reader.onload = () => loadImage(reader.result as string)
    reader.readAsDataURL(f)
  }

  // draw preview: image + dimmed overlay + crop window
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !img) return
    const maxW = 420
    const scale = Math.min(1, maxW / img.naturalWidth)
    canvas.width = img.naturalWidth * scale
    canvas.height = img.naturalHeight * scale
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    ctx.fillStyle = "rgba(0,0,0,0.5)"
    const cx = crop.x * canvas.width
    const cy = crop.y * canvas.height
    const cw = crop.w * canvas.width
    const ch = crop.h * canvas.height
    // dim everything outside the crop window
    ctx.fillRect(0, 0, canvas.width, cy)
    ctx.fillRect(0, cy, cx, ch)
    ctx.fillRect(cx + cw, cy, canvas.width - cx - cw, ch)
    ctx.fillRect(0, cy + ch, canvas.width, canvas.height - cy - ch)
    ctx.strokeStyle = "#fff"
    ctx.lineWidth = 2
    ctx.strokeRect(cx, cy, cw, ch)
  }, [img, crop])

  const pointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!img) return
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = { startX: e.clientX, startY: e.clientY, origX: crop.x, origY: crop.y }
  }
  const pointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!dragRef.current || !canvas) return
    const dx = (e.clientX - dragRef.current.startX) / canvas.width
    const dy = (e.clientY - dragRef.current.startY) / canvas.height
    setCrop((c) => ({
      ...c,
      x: Math.min(Math.max(0, dragRef.current!.origX + dx), 1 - c.w),
      y: Math.min(Math.max(0, dragRef.current!.origY + dy), 1 - c.h),
    }))
  }
  const pointerUp = () => {
    dragRef.current = null
  }

  const zoom = (delta: number) => {
    setCrop((c) => {
      let w = Math.min(1, Math.max(0.1, c.w * (1 + delta)))
      let h = w
      if (!img) return c
      // keep the required output aspect in image-relative coords
      const imgAspect = img.naturalWidth / img.naturalHeight
      h = (w * imgAspect) / aspect
      if (h > 1) {
        h = 1
        w = (aspect * h) / imgAspect
      }
      return {
        w,
        h,
        x: Math.min(Math.max(0, c.x + (c.w - w) / 2), 1 - w),
        y: Math.min(Math.max(0, c.y + (c.h - h) / 2), 1 - h),
      }
    })
  }

  const apply = () => {
    if (!img) return
    const out = document.createElement("canvas")
    out.width = outWidth
    out.height = outHeight
    const ctx = out.getContext("2d")
    if (!ctx) return
    ctx.drawImage(
      img,
      crop.x * img.naturalWidth, crop.y * img.naturalHeight,
      crop.w * img.naturalWidth, crop.h * img.naturalHeight,
      0, 0, outWidth, outHeight,
    )
    onCropped(out.toDataURL("image/jpeg", 0.85), crop)
  }

  return (
    <div className="space-y-3">
      <p className="text-xs font-medium text-muted">{label} — итог {outWidth}x{outHeight}</p>
      <div className="flex flex-wrap items-center gap-2">
        <input type="file" accept="image/*" onChange={onFile} className="text-xs text-muted file:mr-2 file:rounded-full file:border-0 file:bg-panel file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-foreground" />
        <span className="text-xs text-muted">или URL:</span>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://…/image.jpg"
          className="w-44 rounded-xl border border-border bg-panel px-2.5 py-1.5 text-xs text-foreground focus:outline-none"
        />
        <Button variant="secondary" onClick={() => url && loadImage(url)} className="!px-3 !py-1.5 !text-xs">
          Загрузить
        </Button>
      </div>
      {error ? <p className="text-xs text-danger">{error}</p> : null}
      {img ? (
        <div className="space-y-2">
          <canvas
            ref={canvasRef}
            onPointerDown={pointerDown}
            onPointerMove={pointerMove}
            onPointerUp={pointerUp}
            className="max-w-full cursor-move touch-none rounded-xl border border-border"
          />
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={() => zoom(0.15)} className="!px-3 !py-1.5 !text-xs">Область +</Button>
            <Button variant="secondary" onClick={() => zoom(-0.15)} className="!px-3 !py-1.5 !text-xs">Область −</Button>
            <Button onClick={apply} className="!px-3 !py-1.5 !text-xs">Применить кадр</Button>
          </div>
          <p className="text-[11px] text-muted">Перетащите рамку, чтобы выбрать нужную часть картинки</p>
        </div>
      ) : null}
    </div>
  )
}
