/**
 * ТЗ п.3: единый источник списка рекламодателей (аккаунтов DSP) для всех экранов —
 * «Рекламодатели», «Кампании», мастер «Новая кампания», «Доступы». Один ключ кэша
 * SWR, поэтому после создания/изменения/удаления достаточно refreshAdvertisers().
 * Список не фильтруется: показываются все рекламодатели в зоне видимости пользователя.
 */
import useSWR, { mutate } from "swr"
import { fetcher } from "@/lib/api"
import type { Advertiser } from "@/lib/types"

export const ADVERTISERS_KEY = "/advertisers"

export function useAdvertisers() {
  return useSWR<Advertiser[]>(ADVERTISERS_KEY, fetcher)
}

export function refreshAdvertisers() {
  return mutate(ADVERTISERS_KEY)
}

const STATUS_NOTE: Record<string, string> = {
  paused: "приостановлен",
  blocked: "заблокирован",
}

/**
 * Подпись в выпадающих списках. Неактивные рекламодатели не скрываются и не
 * блокируются при выборе — только помечаются (ограничение сработает при запуске).
 */
export function advertiserOptionLabel(a: Pick<Advertiser, "name" | "status">): string {
  const note = STATUS_NOTE[a.status]
  return note ? `${a.name} (${note})` : a.name
}
