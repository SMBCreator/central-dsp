// Package targeting: фильтрация кампаний по bid request.
// Порт 1:1 backend/routers/rtb.py::_match_targeting (источник истины).
package targeting

import (
	"strconv"
	"strings"
	"time"
)

// DeviceTypes — OpenRTB device.devicetype -> имя (DEVICE_TYPES в Python).
var DeviceTypes = map[int]string{
	1: "mobile", 2: "desktop", 3: "ctv", 4: "phone", 5: "tablet", 6: "connected", 7: "settopbox",
}

// ConnectionTypes — OpenRTB device.connectiontype -> имя (CONNECTION_TYPES в Python).
var ConnectionTypes = map[int]string{
	0: "unknown", 1: "ethernet", 2: "wifi", 3: "cellular", 4: "cellular", 5: "cellular", 6: "cellular",
}

// Targeting — таргетинг line item (JSON из фида, ключи как в Python).
type Targeting struct {
	GeoCountries    []string `json:"geo_countries,omitempty"`
	GeoRegions      []string `json:"geo_regions,omitempty"`
	GeoCities       []string `json:"geo_cities,omitempty"`
	DeviceTypes     []string `json:"device_types,omitempty"`
	OS              []string `json:"os,omitempty"`
	Browsers        []string `json:"browsers,omitempty"`
	Languages       []string `json:"languages,omitempty"`
	ConnectionTypes []string `json:"connection_types,omitempty"`
	IABCategories   []string `json:"iab_categories,omitempty"`
	IABBlock        []string `json:"iab_block,omitempty"`
	DomainAllowlist []string `json:"domain_allowlist,omitempty"`
	DomainBlocklist []string `json:"domain_blocklist,omitempty"`
	AgeMin          int      `json:"age_min,omitempty"`
	AgeMax          int      `json:"age_max,omitempty"`
	Genders         []string `json:"genders,omitempty"`
	Hours           []int    `json:"hours,omitempty"`
	Days            []int    `json:"days,omitempty"`
	SSPAllowlist    []string `json:"ssp_allowlist,omitempty"`

	// --- Расширенный инвентарь / контекст (порт хвоста _match_targeting) ---
	TrafficTypes    []string `json:"traffic_types,omitempty"` // "site" | "app"
	Carriers        []string `json:"carriers,omitempty"`
	OSVersionMin    string   `json:"os_version_min,omitempty"`
	AppBundlesBlock []string `json:"app_bundles_block,omitempty"`
	AppBundlesAllow []string `json:"app_bundles_allow,omitempty"`
	PublisherBlock  []string `json:"publisher_block,omitempty"`
	PublisherAllow  []string `json:"publisher_allow,omitempty"`
	KeywordsBlock   []string `json:"keywords_block,omitempty"`
	KeywordsAllow   []string `json:"keywords_allow,omitempty"`
	IPRanges        []string `json:"ip_ranges,omitempty"` // префиксы: "192.168." / "2a00:"
	BlockCOPPA      bool     `json:"block_coppa,omitempty"`
	BlockGDPR       bool     `json:"block_gdpr,omitempty"`
	SkipDNT         bool     `json:"skip_dnt,omitempty"`
	DeviceMakes     []string `json:"device_makes,omitempty"`    // device.make без регистра (apple, samsung, xiaomi)
	SChainRequired  bool     `json:"schain_required,omitempty"` // source.schain обязателен и complete=1

	// --- Контекст v2 (Фаза 2): все ключи необязательны ---
	IABAllowUnknown bool   `json:"iab_allow_unknown,omitempty"` // тематика не определена — проверка IAB считается пройденной (только context_mode=all)
	KeywordsMatch   string `json:"keywords_match,omitempty"`    // "substring" | "word_start" | "whole_word"; пусто = легаси-слова
	ContextMode     string `json:"context_mode,omitempty"`      // "all" (по умолчанию) | "any": тематика ИЛИ слова

	kw *kwPrepared // слова v2, подготовленные PrepareKeywords при загрузке фида
}

// ImpTargeting — таргетинг уровня impression (порт _match_imp_targeting).
type ImpTargeting struct {
	ViewabilityMin      int      `json:"viewability_min,omitempty"`  // imp.metric[type=viewability].value*100 >= N; нет метрики = мимо
	VideoPlacements     []int    `json:"video_placements,omitempty"` // video.plcmt (2.6) | video.placement (2.5)
	VideoProtocols      []int    `json:"video_protocols,omitempty"`  // пересечение с video.protocols; пустой список у SSP = пропускаем
	BannerPositions     []int    `json:"banner_positions,omitempty"`
	InterstitialOnly    bool     `json:"interstitial_only,omitempty"`
	ExcludeInterstitial bool     `json:"exclude_interstitial,omitempty"`
	RewardedOnly        bool     `json:"rewarded_only,omitempty"`
	SecureOnly          bool     `json:"secure_only,omitempty"`
	VideoMinDuration    int      `json:"video_min_duration,omitempty"`
	VideoSkippableOnly  bool     `json:"video_skippable_only,omitempty"`
	DealIDs             []string `json:"deal_ids,omitempty"`
}

// RequestView — плоское представление BidRequest для матчинга
// (собирается один раз на запрос, чтобы не парсить JSON в каждом line item).
type RequestView struct {
	Country    string   // device.geo.country (upper)
	Region     string   // device.geo.region (lower)
	City       string   // device.geo.city (lower)
	DeviceType int      // device.devicetype
	OS         string   // device.os (lower, после _device_os)
	OSV        string   // device.osv (как есть)
	UA         string   // device.ua (lower)
	Language   string   // device.language (lower)
	ConnType   int      // device.connectiontype
	Carrier    string   // device.carrier (lower)
	IP         string   // device.ip, иначе device.ipv6
	DNT        bool     // device.dnt == 1 || device.lmt == 1
	CatSource  string   // "request" | "domain_map" | "" (категорий нет): откуда SiteCats
	SiteCats   []string // объединение cat/sectioncat/pagecat/content.cat/publisher.cat site и app (CollectCats: upper, без дублей)
	Domain     string   // site.domain | app.bundle (lower)
	Env        string   // "app" если есть req.app, иначе "site"
	Bundle     string   // app.bundle (lower); пусто для web
	PubID      string   // (site|app).publisher.id
	Keywords   string   // lower(" ".join(keywords, content.keywords, page, content.title))
	Gender     string   // user.gender (upper)
	YOB        int      // user.yob
	COPPA      bool     // regs.coppa == 1
	GDPR       bool     // regs.gdpr == 1 || regs.ext.gdpr == 1
	Make       string   // device.make (lower)
	SChainOK   bool     // source.schain (или source.ext.schain) есть, complete==1, nodes>0
	Now        time.Time

	KWSrc KWSources // сырые источники текста для слов v2 (keywords, content.keywords, page, title)
	kwx   *kwText   // текст запроса для слов v2: строится лениво, один раз на запрос
}

// ImpView — плоское представление одного imp для MatchImp.
type ImpView struct {
	Pos              *int // banner.pos, иначе video.pos; nil = не передан
	Interstitial     bool // imp.instl == 1
	Rewarded         bool // imp.rwdd == 1 || imp.ext.rewarded == 1
	Secure           bool // imp.secure == 1
	HasVideo         bool
	VideoMaxDuration int  // video.maxduration
	VideoSkippable   bool // video.skip == 1
	VideoPlacement   int  // video.plcmt, иначе video.placement; 0 = не передан
	VideoProtocols   []int
	Viewability      int // imp.metric type=viewability, в процентах 0..100; -1 = метрики нет
	DealIDs          []string
}

// DeviceOS — порт _device_os: явный device.os, иначе UA-sniff.
func DeviceOS(osField, ua string) string {
	if v := strings.ToLower(strings.TrimSpace(osField)); v != "" {
		return v
	}
	l := strings.ToLower(ua)
	for _, m := range []struct{ marker, name string }{
		{"android", "android"}, {"iphone", "ios"}, {"ipad", "ios"},
		{"windows", "windows"}, {"mac os", "macos"}, {"linux", "linux"},
	} {
		if strings.Contains(l, m.marker) {
			return m.name
		}
	}
	return "unknown"
}

// DeviceBrowser — порт _device_browser (rtb.py:_BROWSER_MARKERS): семейство
// браузера по UA. Порядок маркеров важен: Chrome-based браузеры содержат
// "chrome" в UA, поэтому yandex/edge/opera/samsung проверяются раньше.
func DeviceBrowser(ua string) string {
	l := strings.ToLower(ua)
	for _, m := range []struct{ marker, name string }{
		{"yabrowser", "yandex"}, {"edg", "edge"}, {"opr", "opera"}, {"opera", "opera"},
		{"samsungbrowser", "samsung"}, {"firefox", "firefox"}, {"chrome", "chrome"}, {"safari", "safari"},
	} {
		if strings.Contains(l, m.marker) {
			return m.name
		}
	}
	return "unknown"
}

// Reason — причина отказа Match: какая группа проверок отсекла кандидата.
// Значения (кроме ReasonNone) идут в метку reason метрики
// bidder_targeting_reject_total; порядок и имена зафиксированы тестом против
// metrics.TargetingRejectReasons.
type Reason uint8

const (
	ReasonNone            Reason = iota // кандидат прошёл
	ReasonGeo                           // страна / регион / город
	ReasonDevice                        // тип устройства, ОС, браузер, язык, соединение, оператор, версия ОС, производитель, IP
	ReasonInventoryIAB                  // тематика площадки (IAB allow / block)
	ReasonInventoryDomain               // белый/чёрный список доменов
	ReasonInventoryOther                // среда site/app, бандлы, паблишеры, schain
	ReasonAudience                      // пол и возраст
	ReasonTime                          // часы и дни недели
	ReasonKeywords                      // контекстные слова
	ReasonContext                       // режим «ИЛИ»: не сработали ни тематика, ни слова
	ReasonRegulatory                    // COPPA / GDPR / DNT
	ReasonOther                         // не классифицировано
	ReasonCount
)

var reasonNames = [ReasonCount]string{
	"", "geo", "device", "inventory_iab", "inventory_domain", "inventory_other",
	"audience", "time", "keywords", "context", "regulatory", "other",
}

// String — значение метки reason (пусто для ReasonNone).
func (r Reason) String() string {
	if r >= ReasonCount {
		return ""
	}
	return reasonNames[r]
}

// Match — порт _match_targeting. Все ключи опциональны; пусто = нет ограничения.
// Обёртка над MatchReason: результат всегда совпадает (reason == ReasonNone).
func Match(t *Targeting, r *RequestView) bool {
	return MatchReason(t, r) == ReasonNone
}

// MatchReason — то же, что Match, но возвращает причину первого отказа.
// Порядок проверок совпадает с Python-эталоном; ReasonNone — кандидат проходит.
func MatchReason(t *Targeting, r *RequestView) Reason {
	if t == nil {
		return ReasonNone
	}
	// Контекст v2: слова v2 и режим «ИЛИ». Для line item без keywords_match и
	// context_mode флаги остаются нулевыми и работает прежний путь (две проверки длины строк).
	var kp *kwPrepared
	var anyMode, combined bool
	if t.KeywordsMatch != "" || t.ContextMode != "" {
		if t.KeywordsMatch != "" {
			kp = t.kwState()
		}
		anyMode = t.ContextMode == "any"
		// combined: «тематика ИЛИ слова» — разрешающие части объединяются ниже,
		// на месте проверки слов, после обоих стоп-условий.
		combined = anyMode && len(t.IABCategories) > 0 && hasAllowWords(t, kp)
	}
	// --- Geo ---
	if len(t.GeoCountries) > 0 && !containsFold(t.GeoCountries, r.Country, NormalizeCountry) {
		return ReasonGeo
	}
	if len(t.GeoRegions) > 0 && !containsFold(t.GeoRegions, r.Region, strings.ToLower) {
		return ReasonGeo
	}
	if len(t.GeoCities) > 0 && !containsFold(t.GeoCities, r.City, strings.ToLower) {
		return ReasonGeo
	}
	// --- Device ---
	if len(t.DeviceTypes) > 0 {
		dt, ok := DeviceTypes[r.DeviceType]
		if !ok {
			dt = "unknown"
		}
		normalized := dt
		if dt == "phone" {
			normalized = "mobile"
		}
		if !contains(t.DeviceTypes, normalized) && !contains(t.DeviceTypes, dt) {
			return ReasonDevice
		}
	}
	if len(t.OS) > 0 && !containsFold(t.OS, r.OS, strings.ToLower) {
		return ReasonDevice
	}
	if len(t.Browsers) > 0 {
		found := false
		for _, b := range t.Browsers {
			if strings.Contains(r.UA, strings.ToLower(b)) {
				found = true
				break
			}
		}
		if !found {
			return ReasonDevice
		}
	}
	if len(t.Languages) > 0 && !containsFold(t.Languages, r.Language, strings.ToLower) {
		return ReasonDevice
	}
	if len(t.ConnectionTypes) > 0 {
		ct, ok := ConnectionTypes[r.ConnType]
		if !ok {
			ct = "unknown"
		}
		if !contains(t.ConnectionTypes, ct) {
			return ReasonDevice
		}
	}
	// --- Inventory ---
	if len(t.IABCategories) > 0 && !combined && !IABIntersects(t.IABCategories, r.SiteCats) {
		// «Без тематики»: тематика запроса пуста и опция включена. В режиме any
		// опция игнорируется.
		if !(t.IABAllowUnknown && !anyMode && len(r.SiteCats) == 0) {
			return ReasonInventoryIAB
		}
	}
	if len(t.IABBlock) > 0 && IABIntersects(t.IABBlock, r.SiteCats) {
		return ReasonInventoryIAB
	}
	if r.Domain != "" && len(t.DomainBlocklist) > 0 && containsFold(t.DomainBlocklist, r.Domain, strings.ToLower) {
		return ReasonInventoryDomain
	}
	if len(t.DomainAllowlist) > 0 && !containsFold(t.DomainAllowlist, r.Domain, strings.ToLower) {
		return ReasonInventoryDomain
	}
	// --- Audience ---
	if len(t.Genders) > 0 && !containsFold(t.Genders, r.Gender, strings.ToUpper) {
		return ReasonAudience
	}
	if (t.AgeMin > 0 || t.AgeMax > 0) && r.YOB > 0 {
		age := r.Now.UTC().Year() - r.YOB
		if t.AgeMin > 0 && age < t.AgeMin {
			return ReasonAudience
		}
		if t.AgeMax > 0 && age > t.AgeMax {
			return ReasonAudience
		}
	}
	// --- Dayparting (UTC) ---
	now := r.Now.UTC()
	if len(t.Hours) > 0 && !containsInt(t.Hours, now.Hour()) {
		return ReasonTime
	}
	if len(t.Days) > 0 {
		// Python weekday(): 0=Mon..6=Sun; Go Weekday(): 0=Sun..6=Sat.
		pyWeekday := (int(now.Weekday()) + 6) % 7
		if !containsInt(t.Days, pyWeekday) {
			return ReasonTime
		}
	}
	// --- Inventory environment: site (web) vs app ---
	if len(t.TrafficTypes) > 0 && !contains(t.TrafficTypes, r.Env) {
		return ReasonInventoryOther
	}
	// --- Carrier / ISP: подстрока в любую сторону, пустой carrier = мимо ---
	if len(t.Carriers) > 0 {
		if r.Carrier == "" {
			return ReasonDevice
		}
		found := false
		for _, c := range t.Carriers {
			lc := strings.ToLower(c)
			if lc == "" {
				continue
			}
			if strings.Contains(r.Carrier, lc) || strings.Contains(lc, r.Carrier) {
				found = true
				break
			}
		}
		if !found {
			return ReasonDevice
		}
	}
	// --- Minimum OS version (major.minor, семантика кортежей Python) ---
	if t.OSVersionMin != "" {
		if r.OSV == "" || compareVersion(r.OSV, t.OSVersionMin) < 0 {
			return ReasonDevice
		}
	}
	// --- App bundle allow/block (отдельно от web-доменов) ---
	if r.Bundle != "" && len(t.AppBundlesBlock) > 0 && containsFold(t.AppBundlesBlock, r.Bundle, strings.ToLower) {
		return ReasonInventoryOther
	}
	if len(t.AppBundlesAllow) > 0 && !containsFold(t.AppBundlesAllow, r.Bundle, strings.ToLower) {
		return ReasonInventoryOther
	}
	// --- Publisher allow/block: точное сравнение id ---
	if r.PubID != "" && len(t.PublisherBlock) > 0 && contains(t.PublisherBlock, r.PubID) {
		return ReasonInventoryOther
	}
	if len(t.PublisherAllow) > 0 && !contains(t.PublisherAllow, r.PubID) {
		return ReasonInventoryOther
	}
	// --- Contextual keywords: стоп-слова приоритетнее, подстрока без регистра ---
	// Стоп-слова — всегда первыми и всегда широко (v2: nk в N или T).
	if kp != nil {
		if len(kp.block) > 0 && kp.blockMatch(r.kwTextOnce()) {
			return ReasonKeywords
		}
	} else if len(t.KeywordsBlock) > 0 && anySubstring(t.KeywordsBlock, r.Keywords) {
		return ReasonKeywords
	}
	if combined {
		// iabOK ИЛИ kwOK; слова считаем только если тематика не прошла.
		if !IABIntersects(t.IABCategories, r.SiteCats) {
			var kwOK bool
			if kp != nil {
				kwOK = kp.allowMatch(r.kwTextOnce())
			} else {
				kwOK = anySubstring(t.KeywordsAllow, r.Keywords)
			}
			if !kwOK {
				return ReasonContext
			}
		}
	} else if hasAllowWords(t, kp) {
		var kwOK bool
		if kp != nil {
			kwOK = kp.allowMatch(r.kwTextOnce())
		} else {
			kwOK = anySubstring(t.KeywordsAllow, r.Keywords)
		}
		if !kwOK {
			return ReasonKeywords
		}
	}
	// --- IP prefix ranges ---
	if len(t.IPRanges) > 0 {
		found := false
		for _, p := range t.IPRanges {
			if p != "" && strings.HasPrefix(r.IP, p) {
				found = true
				break
			}
		}
		if !found {
			return ReasonDevice
		}
	}
	// --- Regulatory ---
	if t.BlockCOPPA && r.COPPA {
		return ReasonRegulatory
	}
	if t.BlockGDPR && r.GDPR {
		return ReasonRegulatory
	}
	if t.SkipDNT && r.DNT {
		return ReasonRegulatory
	}
	// --- Device make: без регистра, пустой make у SSP = мимо (как carriers) ---
	if len(t.DeviceMakes) > 0 {
		if r.Make == "" || !containsFold(t.DeviceMakes, r.Make, strings.ToLower) {
			return ReasonDevice
		}
	}
	// --- Supply chain: только запросы с полной schain ---
	if t.SChainRequired && !r.SChainOK {
		return ReasonInventoryOther
	}
	return ReasonNone
}

// MatchImp — порт _match_imp_targeting: позиция, interstitial, rewarded,
// secure, параметры видео, PMP-сделки. nil таргетинг = без ограничений.
func MatchImp(t *ImpTargeting, v *ImpView) bool {
	if t == nil {
		return true
	}
	if len(t.BannerPositions) > 0 {
		if v.Pos == nil || !containsInt(t.BannerPositions, *v.Pos) {
			return false
		}
	}
	if t.InterstitialOnly && !v.Interstitial {
		return false
	}
	if t.ExcludeInterstitial && v.Interstitial {
		return false
	}
	if t.RewardedOnly && !v.Rewarded {
		return false
	}
	if t.SecureOnly && !v.Secure {
		return false
	}
	if v.HasVideo {
		if t.VideoMinDuration > 0 && v.VideoMaxDuration > 0 && v.VideoMaxDuration < t.VideoMinDuration {
			return false
		}
		if t.VideoSkippableOnly && !v.VideoSkippable {
			return false
		}
		// plcmt: SSP не передал (0) — считаем «неизвестно» и пропускаем только
		// если фильтр не задан; задан фильтр + нет plcmt = мимо (клиент платит
		// за конкретное размещение, неизвестное — не то).
		if len(t.VideoPlacements) > 0 && !containsInt(t.VideoPlacements, v.VideoPlacement) {
			return false
		}
		// protocols: SSP не передал список — принимает любые, пропускаем.
		if len(t.VideoProtocols) > 0 && len(v.VideoProtocols) > 0 && !intersectsInt(t.VideoProtocols, v.VideoProtocols) {
			return false
		}
	}
	// Viewability: фильтр задан, метрики нет (-1) — мимо; иначе сравнение в процентах.
	if t.ViewabilityMin > 0 && v.Viewability < t.ViewabilityMin {
		return false
	}
	if len(t.DealIDs) > 0 && MatchedDeal(t.DealIDs, v.DealIDs) == "" {
		return false
	}
	return true
}

func intersectsInt(a, b []int) bool {
	for _, x := range a {
		if containsInt(b, x) {
			return true
		}
	}
	return false
}

// MatchedDeal — порт _matched_deal: первый настроенный deal id, присутствующий
// в imp.pmp.deals. Пусто, если deal_ids не заданы или пересечения нет.
func MatchedDeal(wanted, present []string) string {
	if len(wanted) == 0 {
		return ""
	}
	for _, p := range present {
		if p != "" && contains(wanted, p) {
			return p
		}
	}
	return ""
}

// compareVersion сравнивает "major.minor" как кортежи Python:
// берутся первые два числовых компонента, более короткий кортеж меньше
// при равном префиксе ((16,) < (16,4)). Нечисловой компонент -> -1 (как
// ValueError в Python: line item не матчится).
func compareVersion(a, b string) int {
	pa, okA := versionTuple(a)
	pb, okB := versionTuple(b)
	if !okA || !okB {
		return -1
	}
	for i := 0; i < len(pa) && i < len(pb); i++ {
		if pa[i] != pb[i] {
			if pa[i] < pb[i] {
				return -1
			}
			return 1
		}
	}
	switch {
	case len(pa) < len(pb):
		return -1
	case len(pa) > len(pb):
		return 1
	}
	return 0
}

func versionTuple(v string) ([]int, bool) {
	parts := strings.Split(strings.TrimSpace(v), ".")
	if len(parts) > 2 {
		parts = parts[:2]
	}
	out := make([]int, 0, len(parts))
	for _, p := range parts {
		n, err := strconv.Atoi(strings.TrimSpace(p))
		if err != nil {
			return nil, false
		}
		out = append(out, n)
	}
	return out, true
}

// KeywordHaystack — порт сборки kw_haystack: lower(" ".join(...)).
func KeywordHaystack(parts ...string) string {
	return strings.ToLower(strings.Join(parts, " "))
}

// MatchSSP — ssp_allowlist: пусто = все SSP разрешены.
func MatchSSP(allowlist []string, partnerID string) bool {
	if len(allowlist) == 0 {
		return true
	}
	return contains(allowlist, partnerID)
}

// MatchDomain проверяет домен против white/blacklist кампании и глобальных списков.
// Семантика Python: blacklist имеет приоритет; whitelist (если непустой) — строгий
// allowlist. Сравнение case-insensitive, суффиксное (sub.example.com матчит example.com).
func MatchDomain(domain string, whitelist, blacklist []string) bool {
	d := strings.ToLower(strings.TrimSpace(domain))
	if d == "" {
		return len(whitelist) == 0
	}
	for _, b := range blacklist {
		if matchesSuffix(d, strings.ToLower(b)) {
			return false
		}
	}
	if len(whitelist) == 0 {
		return true
	}
	for _, w := range whitelist {
		if matchesSuffix(d, strings.ToLower(w)) {
			return true
		}
	}
	return false
}

func matchesSuffix(domain, pattern string) bool {
	if domain == pattern {
		return true
	}
	return strings.HasSuffix(domain, "."+pattern)
}

// MatchGeo — country-таргетинг (упрощённый интерфейс, используется тестами каркаса).
func MatchGeo(country string, targets []string) bool {
	if len(targets) == 0 {
		return true
	}
	c := strings.ToUpper(strings.TrimSpace(country))
	for _, t := range targets {
		if strings.ToUpper(t) == c {
			return true
		}
	}
	return false
}

// ImpAdType — порт _imp_ad_type: push (через ext) до generic native.
func ImpAdType(hasBanner, hasNative, hasVideo, extPush bool) string {
	switch {
	case extPush:
		return "push"
	case hasBanner:
		return "banner"
	case hasNative:
		return "native"
	case hasVideo:
		return "video"
	default:
		return ""
	}
}

// --- helpers ---

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

func containsFold(list []string, v string, norm func(string) string) bool {
	nv := norm(v)
	for _, x := range list {
		if norm(x) == nv {
			return true
		}
	}
	return false
}

func containsInt(list []int, v int) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

// anySubstring: хотя бы одна непустая needle (lower) входит в haystack (уже lower).
func anySubstring(needles []string, haystack string) bool {
	for _, n := range needles {
		ln := strings.ToLower(strings.TrimSpace(n))
		if ln != "" && strings.Contains(haystack, ln) {
			return true
		}
	}
	return false
}

// hasAllowWords: заданы разрешающие слова (v2 — после очистки пустых и лишних).
func hasAllowWords(t *Targeting, kp *kwPrepared) bool {
	if kp != nil {
		return len(kp.allow) > 0
	}
	return len(t.KeywordsAllow) > 0
}
