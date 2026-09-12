package prefilter

// Зеркало backend/tests/test_prefilter.py: одинаковые входы -> одинаковые
// причины отсечения в обоих биддерах.

import (
	"context"
	"errors"
	"testing"
	"time"
)

func defaultCfg() Config {
	return Config{
		Enabled:         true,
		Countries:       map[string]bool{"ru": true, "rus": true},
		RequireDeviceID: true,
		DCCacheTTL:      time.Minute,
	}
}

func legit() Device {
	return Device{UA: "Mozilla/5.0 (Linux; Android 13) Chrome/120", Country: "RU", IFA: "ifa-1", IP: "5.6.7.8"}
}

type fakeSet struct {
	members []string
	err     error
	calls   int
}

func (f *fakeSet) SMembers(context.Context, string) ([]string, error) {
	f.calls++
	return f.members, f.err
}

func TestLegitRequestPasses(t *testing.T) {
	if r := New(defaultCfg(), nil).Check(legit()); r != "" {
		t.Fatalf("legit request dropped: %s", r)
	}
}

func TestEmptyUAAndMissingCountryPass(t *testing.T) {
	f := New(defaultCfg(), nil)
	d := legit()
	d.UA = ""
	if r := f.Check(d); r != "" {
		t.Fatalf("empty UA must pass, got %s", r)
	}
	d = legit()
	d.Country = ""
	if r := f.Check(d); r != "" {
		t.Fatalf("missing country must pass, got %s", r)
	}
}

func TestBotUADropped(t *testing.T) {
	f := New(defaultCfg(), nil)
	for _, ua := range []string{
		"Googlebot/2.1 (+http://www.google.com/bot.html)",
		"python-requests/2.31.0",
		"curl/8.4.0",
		"Mozilla/5.0 HeadlessChrome/119.0",
		"okhttp/4.12.0",
	} {
		d := legit()
		d.UA = ua
		if r := f.Check(d); r != ReasonBotUA {
			t.Errorf("ua %q: want bot_ua, got %q", ua, r)
		}
	}
}

func TestGeo(t *testing.T) {
	f := New(defaultCfg(), nil)
	for _, c := range []string{"USA", "DE", "cn"} {
		d := legit()
		d.Country = c
		if r := f.Check(d); r != ReasonGeoUnserved {
			t.Errorf("country %q: want geo_unserved, got %q", c, r)
		}
	}
	for _, c := range []string{"RU", "ru", "RUS", "rus"} {
		d := legit()
		d.Country = c
		if r := f.Check(d); r != "" {
			t.Errorf("country %q must pass, got %q", c, r)
		}
	}
	// Пустой список стран = гео-фильтр выключен.
	cfg := defaultCfg()
	cfg.Countries = map[string]bool{}
	d := legit()
	d.Country = "USA"
	if r := New(cfg, nil).Check(d); r != "" {
		t.Fatalf("empty THROTTLE_COUNTRIES must disable geo filter, got %q", r)
	}
}

func TestNoDeviceID(t *testing.T) {
	f := New(defaultCfg(), nil)
	d := legit()
	d.IFA, d.IP, d.IPv6 = "", "", ""
	if r := f.Check(d); r != ReasonNoDeviceID {
		t.Fatalf("want no_device_id, got %q", r)
	}
	d.IP = "1.2.3.4"
	if r := f.Check(d); r != "" {
		t.Fatalf("ip alone is a device id, got %q", r)
	}
	cfg := defaultCfg()
	cfg.RequireDeviceID = false
	d.IP = ""
	if r := New(cfg, nil).Check(d); r != "" {
		t.Fatalf("THROTTLE_REQUIRE_DEVICE_ID=0 must pass, got %q", r)
	}
}

func TestDatacenterPrefixes(t *testing.T) {
	src := &fakeSet{members: []string{"45.155.204.", "10.20."}}
	f := New(defaultCfg(), src)
	f.Refresh()
	for ip, want := range map[string]Reason{
		"45.155.204.77": ReasonDatacenterIP, // /24
		"10.20.1.2":     ReasonDatacenterIP, // /16
		"45.155.1.2":    "",                 // соседняя /24 не в списке
		"8.8.8.8":       "",
	} {
		d := legit()
		d.IP = ip
		if r := f.Check(d); r != want {
			t.Errorf("ip %s: want %q, got %q", ip, want, r)
		}
	}
	// IPv6 в datacenter-списке не поддерживается (как в Python).
	d := legit()
	d.IP = ""
	d.IPv6 = "2001:db8::1"
	if r := f.Check(d); r != "" {
		t.Fatalf("ipv6 must not match dc list, got %q", r)
	}
}

func TestDatacenterColdCacheAndRedisErrorFailOpen(t *testing.T) {
	// Холодный кэш: первый запрос пропускается, прогрев запущен в фоне.
	src := &fakeSet{members: []string{"45.155.204."}}
	f := New(defaultCfg(), src)
	d := legit()
	d.IP = "45.155.204.77"
	if r := f.Check(d); r != "" {
		t.Fatalf("cold cache must fail-open, got %q", r)
	}
	// Redis недоступен: stale/пустой кэш, запрос проходит, без паники.
	bad := &fakeSet{err: errors.New("down")}
	f = New(defaultCfg(), bad)
	f.Refresh()
	if r := f.Check(d); r != "" {
		t.Fatalf("redis error must fail-open, got %q", r)
	}
	// Без Redis вообще — datacenter-проверка выключена.
	if r := New(defaultCfg(), nil).Check(d); r != "" {
		t.Fatalf("nil redis must fail-open, got %q", r)
	}
}

func TestDisabled(t *testing.T) {
	cfg := defaultCfg()
	cfg.Enabled = false
	d := legit()
	d.UA = "curl/8.4.0"
	if r := New(cfg, nil).Check(d); r != "" {
		t.Fatalf("THROTTLE_ENABLED=0 must pass everything, got %q", r)
	}
	var nilFilter *Filter
	if r := nilFilter.Check(d); r != "" {
		t.Fatalf("nil filter must pass, got %q", r)
	}
}

func TestFromEnvDefaults(t *testing.T) {
	t.Setenv("THROTTLE_ENABLED", "")
	t.Setenv("THROTTLE_COUNTRIES", "")
	t.Setenv("THROTTLE_REQUIRE_DEVICE_ID", "no")
	t.Setenv("THROTTLE_DC_CACHE_TTL", "2.5")
	cfg := FromEnv()
	if cfg.Enabled {
		t.Fatal("THROTTLE_ENABLED='' means disabled (Python _env_bool)")
	}
	if len(cfg.Countries) != 0 {
		t.Fatal("explicit empty THROTTLE_COUNTRIES disables geo filter")
	}
	if cfg.RequireDeviceID {
		t.Fatal("'no' must disable device-id requirement")
	}
	if cfg.DCCacheTTL != 2500*time.Millisecond {
		t.Fatalf("ttl: %v", cfg.DCCacheTTL)
	}
}
