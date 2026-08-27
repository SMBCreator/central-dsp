// Package targeting: фильтрация кампаний по bid request.
// Порт 1:1 backend/routers/rtb.py::_match_targeting (источник истины).
package targeting

import (
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
}

// RequestView — плоское представление BidRequest для матчинга
// (собирается один раз на запрос, чтобы не парсить JSON в каждом line item).
type RequestView struct {
	Country    string   // device.geo.country (upper)
	Region     string   // device.geo.region (lower)
	City       string   // device.geo.city (lower)
	DeviceType int      // device.devicetype
	OS         string   // device.os (lower, после _device_os)
	UA         string   // device.ua (lower)
	Language   string   // device.language (lower)
	ConnType   int      // device.connectiontype
	SiteCats   []string // site.cat | app.cat (upper)
	Domain     string   // site.domain | app.bundle (lower)
	Gender     string   // user.gender (upper)
	YOB        int      // user.yob
	Now        time.Time
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

// Match — порт _match_targeting. Все ключи опциональны; пусто = нет ограничения.
func Match(t *Targeting, r *RequestView) bool {
	if t == nil {
		return true
	}
	// --- Geo ---
	if len(t.GeoCountries) > 0 && !containsFold(t.GeoCountries, r.Country, strings.ToUpper) {
		return false
	}
	if len(t.GeoRegions) > 0 && !containsFold(t.GeoRegions, r.Region, strings.ToLower) {
		return false
	}
	if len(t.GeoCities) > 0 && !containsFold(t.GeoCities, r.City, strings.ToLower) {
		return false
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
			return false
		}
	}
	if len(t.OS) > 0 && !containsFold(t.OS, r.OS, strings.ToLower) {
		return false
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
			return false
		}
	}
	if len(t.Languages) > 0 && !containsFold(t.Languages, r.Language, strings.ToLower) {
		return false
	}
	if len(t.ConnectionTypes) > 0 {
		ct, ok := ConnectionTypes[r.ConnType]
		if !ok {
			ct = "unknown"
		}
		if !contains(t.ConnectionTypes, ct) {
			return false
		}
	}
	// --- Inventory ---
	if len(t.IABCategories) > 0 && !intersectsUpper(t.IABCategories, r.SiteCats) {
		return false
	}
	if len(t.IABBlock) > 0 && intersectsUpper(t.IABBlock, r.SiteCats) {
		return false
	}
	if r.Domain != "" && len(t.DomainBlocklist) > 0 && containsFold(t.DomainBlocklist, r.Domain, strings.ToLower) {
		return false
	}
	if len(t.DomainAllowlist) > 0 && !containsFold(t.DomainAllowlist, r.Domain, strings.ToLower) {
		return false
	}
	// --- Audience ---
	if len(t.Genders) > 0 && !containsFold(t.Genders, r.Gender, strings.ToUpper) {
		return false
	}
	if (t.AgeMin > 0 || t.AgeMax > 0) && r.YOB > 0 {
		age := r.Now.UTC().Year() - r.YOB
		if t.AgeMin > 0 && age < t.AgeMin {
			return false
		}
		if t.AgeMax > 0 && age > t.AgeMax {
			return false
		}
	}
	// --- Dayparting (UTC) ---
	now := r.Now.UTC()
	if len(t.Hours) > 0 && !containsInt(t.Hours, now.Hour()) {
		return false
	}
	if len(t.Days) > 0 {
		// Python weekday(): 0=Mon..6=Sun; Go Weekday(): 0=Sun..6=Sat.
		pyWeekday := (int(now.Weekday()) + 6) % 7
		if !containsInt(t.Days, pyWeekday) {
			return false
		}
	}
	return true
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

func intersectsUpper(a, b []string) bool {
	for _, x := range a {
		ux := strings.ToUpper(x)
		for _, y := range b {
			if strings.ToUpper(y) == ux {
				return true
			}
		}
	}
	return false
}
