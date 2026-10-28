/**
 * Единый хук справочника размеров баннеров (GET /creatives/sizes) со встроенным
 * резервным списком из ad-sizes-catalog.ts.
 */
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { FALLBACK_AD_SIZES, type AdSize } from "@/lib/ad-sizes-catalog"

export * from "@/lib/ad-sizes-catalog"

export const AD_SIZES_KEY = "/creatives/sizes"

/** Один ключ SWR на всё приложение; до ответа сервера — резервный список. */
export function useAdSizes(): AdSize[] {
  const { data } = useSWR<AdSize[]>(AD_SIZES_KEY, fetcher, { revalidateOnFocus: false, dedupingInterval: 10 * 60_000 })
  return Array.isArray(data) && data.length > 0 ? data : FALLBACK_AD_SIZES
}
