/* eslint-disable @next/next/no-img-element -- статичные SVG из public/brand, оптимизация next/image не нужна */

export const BRAND_NAME = "Central ADV"

const LOGO = { w: 255, h: 48 }
const MARK = { w: 46, h: 34 }

/**
 * ТЗ п.1: чёрный знак/логотип на светлой теме, белый на тёмной.
 * Тема — класс .dark на <html>, его ставит inline-скрипт в layout до отрисовки,
 * поэтому переключение через CSS (dark:) не мигает.
 */
export function BrandLogo({
  variant = "full",
  height = 28,
  className = "",
  alt = BRAND_NAME,
}: {
  variant?: "full" | "mark"
  height?: number
  className?: string
  alt?: string
}) {
  const size = variant === "full" ? LOGO : MARK
  const width = Math.round((size.w / size.h) * height)
  const base = variant === "full" ? "/brand/logo" : "/brand/mark"
  return (
    <span className={`inline-flex shrink-0 ${className}`} style={{ width, height }}>
      <img src={`${base}-black.svg`} alt={alt} width={width} height={height} className="block dark:hidden" />
      <img src={`${base}-white.svg`} alt={alt} width={width} height={height} className="hidden dark:block" />
    </span>
  )
}
