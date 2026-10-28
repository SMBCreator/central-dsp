/**
 * Запасной каталог макросов ${CDSP_…}: используется, пока GET /creatives/macros
 * не ответил или недоступен. Источник правды — backend/url_macros.json; паритет
 * проверяет tests/url-macros.test.ts.
 */
export type MacroGroup = "ids" | "inventory" | "geo" | "device" | "ad" | "time"

export interface MacroEntry {
  key: string
  macro: string
  group: MacroGroup
  label: string
  param: string
  example: string
  description: string
}

export const MACRO_GROUPS: { key: MacroGroup; label: string }[] = [
  { key: "ids", label: "Идентификаторы" },
  { key: "inventory", label: "Площадка" },
  { key: "geo", label: "География" },
  { key: "device", label: "Устройство" },
  { key: "ad", label: "Объявление" },
  { key: "time", label: "Время" },
]

export const FALLBACK_MACROS: MacroEntry[] = [
  {key: "BID_ID", macro: "${CDSP_BID_ID}", group: "ids", label: "ID ставки", param: "bid", example: "b3f0c2d1-7a41-4e0b-9d52-1c6a8e4f7a10", description: "Уникальный идентификатор нашей ставки"},
  {key: "AUCTION_ID", macro: "${CDSP_AUCTION_ID}", group: "ids", label: "ID аукциона", param: "auction_id", example: "a81c5e92d7", description: "Идентификатор запроса (аукциона) от SSP"},
  {key: "IMP_ID", macro: "${CDSP_IMP_ID}", group: "ids", label: "ID показа в запросе", param: "imp_id", example: "1", description: "Идентификатор рекламного слота внутри запроса SSP"},
  {key: "CAMPAIGN_ID", macro: "${CDSP_CAMPAIGN_ID}", group: "ids", label: "ID кампании", param: "campaign_id", example: "7c1e0b2a", description: "Идентификатор кампании в Central DSP"},
  {key: "GROUP_ID", macro: "${CDSP_GROUP_ID}", group: "ids", label: "ID группы", param: "group_id", example: "9de41f03", description: "Идентификатор группы объявлений (line item)"},
  {key: "CREATIVE_ID", macro: "${CDSP_CREATIVE_ID}", group: "ids", label: "ID креатива", param: "creative_id", example: "6eb2d4a9", description: "Идентификатор креатива в Central DSP"},
  {key: "SITE_ID", macro: "${CDSP_SITE_ID}", group: "inventory", label: "ID сайта или приложения", param: "site_id", example: "12345", description: "Идентификатор сайта или приложения у SSP"},
  {key: "PUBLISHER_ID", macro: "${CDSP_PUBLISHER_ID}", group: "inventory", label: "ID издателя", param: "publisher_id", example: "p-77", description: "Идентификатор издателя у SSP"},
  {key: "PLACEMENT_ID", macro: "${CDSP_PLACEMENT_ID}", group: "inventory", label: "ID рекламного места", param: "placement_id", example: "top_300x250", description: "Идентификатор рекламного места у SSP (imp.tagid)"},
  {key: "DOMAIN", macro: "${CDSP_DOMAIN}", group: "inventory", label: "Домен", param: "domain", example: "news.example.kz", description: "Домен сайта или приложения, строчными буквами"},
  {key: "BUNDLE", macro: "${CDSP_BUNDLE}", group: "inventory", label: "Bundle приложения", param: "bundle", example: "com.example.app", description: "Идентификатор мобильного приложения, строчными буквами"},
  {key: "SSP_ID", macro: "${CDSP_SSP_ID}", group: "inventory", label: "ID SSP-партнёра", param: "ssp_id", example: "686b7a89-3c1d-4f6e-8a20-5d9c1b7e4f02", description: "Внутренний идентификатор SSP-партнёра (не название)"},
  {key: "COUNTRY", macro: "${CDSP_COUNTRY}", group: "geo", label: "Страна", param: "country", example: "KZ", description: "Код страны ISO-3166 alpha-2, определённый по запросу SSP"},
  {key: "REGION", macro: "${CDSP_REGION}", group: "geo", label: "Регион", param: "region", example: "ALA", description: "Регион из геоданных запроса, как передал SSP"},
  {key: "CITY", macro: "${CDSP_CITY}", group: "geo", label: "Город", param: "city", example: "Almaty", description: "Город из геоданных запроса, как передал SSP"},
  {key: "DEVICE_TYPE", macro: "${CDSP_DEVICE_TYPE}", group: "device", label: "Тип устройства", param: "device", example: "mobile", description: "mobile, desktop, tablet, ctv и т. д."},
  {key: "OS", macro: "${CDSP_OS}", group: "device", label: "Операционная система", param: "os", example: "android", description: "Нормализованная ОС строчными буквами"},
  {key: "OS_VERSION", macro: "${CDSP_OS_VERSION}", group: "device", label: "Версия ОС", param: "os_version", example: "14", description: "Версия операционной системы"},
  {key: "BROWSER", macro: "${CDSP_BROWSER}", group: "device", label: "Браузер", param: "browser", example: "chrome", description: "Семейство браузера по User-Agent"},
  {key: "DEVICE_MAKE", macro: "${CDSP_DEVICE_MAKE}", group: "device", label: "Производитель", param: "make", example: "samsung", description: "Производитель устройства строчными буквами"},
  {key: "DEVICE_MODEL", macro: "${CDSP_DEVICE_MODEL}", group: "device", label: "Модель устройства", param: "model", example: "SM-S921B", description: "Модель устройства, как передал SSP"},
  {key: "CARRIER", macro: "${CDSP_CARRIER}", group: "device", label: "Оператор связи", param: "carrier", example: "Kcell", description: "Мобильный оператор, как передал SSP"},
  {key: "CONNECTION", macro: "${CDSP_CONNECTION}", group: "device", label: "Тип соединения", param: "connection", example: "wifi", description: "wifi, cellular, ethernet или unknown"},
  {key: "LANGUAGE", macro: "${CDSP_LANGUAGE}", group: "device", label: "Язык", param: "language", example: "ru", description: "Язык устройства строчными буквами"},
  {key: "AD_FORMAT", macro: "${CDSP_AD_FORMAT}", group: "ad", label: "Формат рекламы", param: "format", example: "banner", description: "banner, native, video или push"},
  {key: "SIZE", macro: "${CDSP_SIZE}", group: "ad", label: "Размер", param: "size", example: "300x250", description: "Ширина и высота креатива, например 300x250"},
  {key: "WIDTH", macro: "${CDSP_WIDTH}", group: "ad", label: "Ширина", param: "width", example: "300", description: "Ширина креатива в пикселях"},
  {key: "HEIGHT", macro: "${CDSP_HEIGHT}", group: "ad", label: "Высота", param: "height", example: "250", description: "Высота креатива в пикселях"},
  {key: "TIMESTAMP", macro: "${CDSP_TIMESTAMP}", group: "time", label: "Время ставки", param: "ts", example: "1793124723", description: "Unix-время ставки в секундах"},
  {key: "CACHEBUSTER", macro: "${CDSP_CACHEBUSTER}", group: "time", label: "Случайное число", param: "cb", example: "48201733", description: "Случайное число против кеширования пикселей"},
]
