package targeting

import "testing"

// Паритет с rtb.py:_BROWSER_MARKERS — порядок маркеров важен, т.к. Chromium-
// браузеры содержат "chrome"/"safari" в UA.
func TestDeviceBrowserParity(t *testing.T) {
	cases := map[string]string{
		"Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 YaBrowser/24.1 Safari/537.36": "yandex",
		"Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Edg/120.0":      "edge",
		"Mozilla/5.0 (X11; Linux) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 OPR/106.0":           "opera",
		"Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-S911B) SamsungBrowser/23.0 Chrome/115 Safari":    "samsung",
		"Mozilla/5.0 (Windows NT 10.0; rv:121.0) Gecko/20100101 Firefox/121.0":                       "firefox",
		"Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36":       "chrome",
		"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15 Version/17.0 Safari/604.1":    "safari",
		"curl/8.4.0": "unknown",
		"":           "unknown",
	}
	for ua, want := range cases {
		if got := DeviceBrowser(ua); got != want {
			t.Errorf("DeviceBrowser(%q) = %q, want %q", ua, got, want)
		}
	}
}

func TestMatchDomainBlacklistWins(t *testing.T) {
	if MatchDomain("bad.example.com", []string{"example.com"}, []string{"bad.example.com"}) {
		t.Fatal("blacklist must win over whitelist")
	}
}

func TestMatchDomainSuffix(t *testing.T) {
	if !MatchDomain("sub.example.com", []string{"example.com"}, nil) {
		t.Fatal("subdomain must match whitelist suffix")
	}
	if MatchDomain("notexample.com", []string{"example.com"}, nil) {
		t.Fatal("notexample.com must NOT match example.com (no substring match)")
	}
}

func TestMatchDomainEmptyWhitelistAllows(t *testing.T) {
	if !MatchDomain("anything.ru", nil, nil) {
		t.Fatal("empty lists must allow")
	}
}

func TestMatchDomainEmptyDomain(t *testing.T) {
	if !MatchDomain("", nil, nil) {
		t.Fatal("empty domain with no whitelist must pass")
	}
	if MatchDomain("", []string{"example.com"}, nil) {
		t.Fatal("empty domain with whitelist must fail")
	}
}

func TestMatchGeo(t *testing.T) {
	if !MatchGeo("RU", []string{"ru", "KZ"}) {
		t.Fatal("case-insensitive geo match expected")
	}
	if MatchGeo("US", []string{"RU"}) {
		t.Fatal("US must not match RU")
	}
	if !MatchGeo("US", nil) {
		t.Fatal("empty targets = allow all")
	}
}
