package targeting

import (
	"os"
	"regexp"
	"testing"
)

func TestNormalizeCountry(t *testing.T) {
	cases := map[string]string{
		"RUS": "RU", "rus": "RU", " RUS ": "RU", "RU": "RU", "ru": "RU",
		"KAZ": "KZ", "BLR": "BY", "XKX": "XK", "ZZZ": "ZZZ", "": "",
	}
	for in, want := range cases {
		if got := NormalizeCountry(in); got != want {
			t.Errorf("NormalizeCountry(%q) = %q, want %q", in, got, want)
		}
	}
}

// F-B1: UI хранит alpha-2, SSP шлёт alpha-3 — оба варианта обязаны совпадать.
func TestMatchCountryAlpha3VsAlpha2(t *testing.T) {
	cases := []struct {
		name    string
		targets []string
		req     string
		want    bool
	}{
		{"alpha-3 request vs alpha-2 target", []string{"RU"}, "RUS", true},
		{"alpha-2 request vs alpha-2 target", []string{"RU"}, "RU", true},
		{"alpha-3 request vs legacy alpha-3 target", []string{"RUS"}, "RUS", true},
		{"lowercase request", []string{"ru"}, "rus", true},
		{"other country", []string{"RU"}, "KAZ", false},
		{"multiple targets", []string{"KZ", "RU"}, "RUS", true},
		{"empty request country", []string{"RU"}, "", false},
	}
	for _, c := range cases {
		v := baseView()
		v.Country = c.req
		if got := Match(&Targeting{GeoCountries: c.targets}, v); got != c.want {
			t.Errorf("%s: Match = %v, want %v", c.name, got, c.want)
		}
	}
}

// Таблица Go обязана совпадать с backend/country_codes.py.
func TestCountryTableParity(t *testing.T) {
	src, err := os.ReadFile("../../../backend/country_codes.py")
	if err != nil {
		t.Skipf("backend not available: %v", err)
	}
	py := map[string]string{}
	for _, m := range regexp.MustCompile(`"([A-Z]{3})": "([A-Z]{2})"`).FindAllStringSubmatch(string(src), -1) {
		py[m[1]] = m[2]
	}
	if len(py) != len(alpha3ToAlpha2) {
		t.Fatalf("size mismatch: python=%d go=%d", len(py), len(alpha3ToAlpha2))
	}
	for k, v := range alpha3ToAlpha2 {
		if py[k] != v {
			t.Errorf("%s: go=%s python=%s", k, v, py[k])
		}
	}
}
