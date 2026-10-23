package targeting

import "strings"

// NormalizeCountry приводит device.geo.country к upper-case ISO alpha-2.
// OpenRTB 2.x присылает alpha-3 ("RUS"), а UI хранит в geo_countries alpha-2
// ("RU") — без нормализации гео-таргетинг по стране не совпадал никогда (F-B1).
// Неизвестный 3-буквенный код возвращается как есть (upper).
// Паритет: backend/country_codes.py::normalize_country.
func NormalizeCountry(code string) string {
	c := strings.ToUpper(strings.TrimSpace(code))
	if len(c) == 3 {
		if a2, ok := alpha3ToAlpha2[c]; ok {
			return a2
		}
	}
	return c
}
