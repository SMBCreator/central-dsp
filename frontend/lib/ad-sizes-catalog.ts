/**
 * Справочник допустимых размеров баннеров — данные без зависимостей (импортируется
 * и клиентом, и серверным mock-роутом). Источник правды — backend/ad_sizes.py
 * (GET /creatives/sizes); паритет закреплён тестом backend/tests/test_ad_sizes.py
 * (TestAdSizesParity) — при правке списка менять обе копии.
 */
export type AdSizeCategory = "desktop" | "mobile" | "universal"

export interface AdSize {
  size: string
  width: number
  height: number
  label: string
  category: AdSizeCategory
  /** Индивидуальный рекомендуемый потолок веса; null — только общий лимит. */
  max_bytes: number | null
}

// prettier-ignore
export const FALLBACK_AD_SIZES: AdSize[] = [
  { size: "120x600", width: 120, height: 600, label: "Skyscraper", category: "desktop", max_bytes: null },
  { size: "160x600", width: 160, height: 600, label: "Wide Skyscraper", category: "desktop", max_bytes: null },
  { size: "200x200", width: 200, height: 200, label: "Small Square", category: "universal", max_bytes: null },
  { size: "240x400", width: 240, height: 400, label: "Vertical Rectangle", category: "desktop", max_bytes: null },
  { size: "240x600", width: 240, height: 600, label: "Vertical Banner", category: "desktop", max_bytes: null },
  { size: "250x250", width: 250, height: 250, label: "Square", category: "universal", max_bytes: null },
  { size: "300x50", width: 300, height: 50, label: "Mobile Banner", category: "mobile", max_bytes: null },
  { size: "300x100", width: 300, height: 100, label: "Mobile Banner Large", category: "mobile", max_bytes: null },
  { size: "300x250", width: 300, height: 250, label: "Medium Rectangle", category: "universal", max_bytes: null },
  { size: "300x300", width: 300, height: 300, label: "Square 300", category: "universal", max_bytes: null },
  { size: "300x500", width: 300, height: 500, label: "Tall Rectangle", category: "universal", max_bytes: null },
  { size: "300x600", width: 300, height: 600, label: "Half Page", category: "desktop", max_bytes: null },
  { size: "320x50", width: 320, height: 50, label: "Mobile Leaderboard", category: "mobile", max_bytes: null },
  { size: "320x100", width: 320, height: 100, label: "Large Mobile Banner", category: "mobile", max_bytes: null },
  { size: "320x480", width: 320, height: 480, label: "Mobile Interstitial", category: "mobile", max_bytes: null },
  { size: "336x280", width: 336, height: 280, label: "Large Rectangle", category: "desktop", max_bytes: null },
  { size: "360x640", width: 360, height: 640, label: "Mobile Fullscreen", category: "mobile", max_bytes: null },
  { size: "468x60", width: 468, height: 60, label: "Full Banner", category: "desktop", max_bytes: null },
  { size: "480x320", width: 480, height: 320, label: "Mobile Interstitial Landscape", category: "mobile", max_bytes: null },
  { size: "500x200", width: 500, height: 200, label: "Wide Rectangle", category: "desktop", max_bytes: null },
  { size: "580x400", width: 580, height: 400, label: "Netboard", category: "desktop", max_bytes: null },
  { size: "728x90", width: 728, height: 90, label: "Leaderboard", category: "desktop", max_bytes: null },
  { size: "970x90", width: 970, height: 90, label: "Large Leaderboard", category: "desktop", max_bytes: null },
  { size: "970x250", width: 970, height: 250, label: "Billboard", category: "desktop", max_bytes: null },
]

export const AD_SIZE_CATEGORY_LABELS: Record<AdSizeCategory, string> = {
  desktop: "Десктоп",
  mobile: "Мобильные",
  universal: "Универсальные",
}

export function formatSize(s: Pick<AdSize, "width" | "height">): string {
  return `${s.width}×${s.height}`
}
