// Package targeting: фильтрация кампаний по bid request.
// Спека: parity_matrix.md (Python backend/routers/rtb.py — источник истины).
package targeting

import "strings"

// MatchDomain проверяет домен против white/blacklist кампании и глобальных списков.
// Семантика Python: blacklist (global -> campaign) имеет приоритет,
// whitelist (если непустой) — строгий allowlist. Сравнение case-insensitive,
// суффиксное (sub.example.com матчит example.com).
func MatchDomain(domain string, whitelist, blacklist []string) bool {
	d := strings.ToLower(strings.TrimSpace(domain))
	if d == "" {
		// Python: пустой домен проходит только при пустом whitelist
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

// MatchGeo — country-таргетинг. TODO(impl): нормализация alpha-3 -> alpha-2
// как в Python (_normalize_country), region/city.
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

// TODO(impl): MatchOS, MatchDeviceType, MatchSSP (ssp_allowlist),
// audiences include/exclude (Redis sets, ifa + user.eids fallback-цепочка).
