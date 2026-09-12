package config

import (
	"strings"
	"testing"
)

const good = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" // 64

func prodOK() map[string]string {
	return map[string]string{
		"APP_ENV":            "production",
		"EVENT_TOKEN_SECRET": good,
		"RTB_API_TOKEN":      strings.Repeat("c", 32),
		"REDIS_URL":          "redis://redis:6379/0",
		"EVENT_BASE_URL":     "https://dsp.example.com",
	}
}

func TestGoodProductionConfigPasses(t *testing.T) {
	warns, err := Assert(MapEnv(prodOK()))
	if err != nil || len(warns) != 0 {
		t.Fatalf("expected clean, got warns=%v err=%v", warns, err)
	}
}

func TestMissingSecretIsFatalInProduction(t *testing.T) {
	for _, name := range []string{"EVENT_TOKEN_SECRET", "RTB_API_TOKEN", "REDIS_URL", "EVENT_BASE_URL"} {
		env := prodOK()
		delete(env, name)
		_, err := Assert(MapEnv(env))
		if err == nil || !strings.Contains(err.Error(), name) {
			t.Errorf("%s: expected fatal mentioning it, got %v", name, err)
		}
	}
}

func TestPlaceholderShortAndWeakValues(t *testing.T) {
	cases := map[string]string{
		"CHANGE_ME_please_replace_1234567890": "плейсхолдер",
		"centraldsp-dev-event-secret":         "слабое",
		"short":                               "короче",
		strings.Repeat("k", 31):               "короче",
		strings.Repeat("x", 40):               "плейсхолдер", // префикс xxx
		"   ":                                 "не задан",
	}
	for val, frag := range cases {
		p := SecretProblem("EVENT_TOKEN_SECRET", val)
		if !strings.Contains(p, frag) {
			t.Errorf("value %q: expected %q in %q", val, frag, p)
		}
	}
	if p := SecretProblem("X", good); p != "" {
		t.Errorf("good value flagged: %q", p)
	}
}

func TestSessionSecretFallbackAccepted(t *testing.T) {
	env := prodOK()
	delete(env, "EVENT_TOKEN_SECRET")
	env["SESSION_SECRET"] = good
	if _, err := Assert(MapEnv(env)); err != nil {
		t.Fatalf("SESSION_SECRET fallback should be accepted: %v", err)
	}
}

func TestInternalFeedTokenFallbackAccepted(t *testing.T) {
	env := prodOK()
	delete(env, "RTB_API_TOKEN")
	env["INTERNAL_FEED_TOKEN"] = strings.Repeat("d", 40)
	if _, err := Assert(MapEnv(env)); err != nil {
		t.Fatalf("INTERNAL_FEED_TOKEN fallback should be accepted: %v", err)
	}
}

func TestOutsideProductionOnlyWarns(t *testing.T) {
	for _, appEnv := range []string{"", "dev", "test", "staging"} {
		env := map[string]string{"APP_ENV": appEnv, "EVENT_TOKEN_SECRET": "change_me"}
		warns, err := Assert(MapEnv(env))
		if err != nil {
			t.Errorf("APP_ENV=%q: must not be fatal, got %v", appEnv, err)
		}
		if len(warns) == 0 {
			t.Errorf("APP_ENV=%q: expected warnings", appEnv)
		}
	}
}

func TestProdDetectionVariants(t *testing.T) {
	if !IsProduction(MapEnv(map[string]string{"APP_ENV": " Prod "})) {
		t.Error("' Prod ' should be production")
	}
	if IsProduction(MapEnv(map[string]string{"APP_ENV": "dev"})) {
		t.Error("dev is not production")
	}
}

func TestEventBaseURLProblem(t *testing.T) {
	bad := []string{
		"",
		"http://backend:8000",   // старый дефолт compose
		"http://localhost:3000", // дефолт rtb.NewHandler
		"http://127.0.0.1:8000",
		"http://10.0.0.5",
		"http://[::1]:8000",
		"https://dsp.internal",
		"dsp.example.com", // без схемы
		"ftp://dsp.example.com",
	}
	for _, b := range bad {
		if p := EventBaseURLProblem(b); p == "" {
			t.Errorf("%q: expected problem", b)
		}
	}
	good := []string{"https://dsp.example.com", "https://rtb.dsp.example.com/", "http://203.0.113.10:8080"}
	for _, g := range good {
		if p := EventBaseURLProblem(g); p != "" {
			t.Errorf("%q: unexpected problem %q", g, p)
		}
	}
}

func TestEventBaseURLFallbackAndDevWarning(t *testing.T) {
	env := prodOK()
	delete(env, "EVENT_BASE_URL")
	env["BACKEND_BASE_URL"] = "https://dsp.example.com"
	if _, err := Assert(MapEnv(env)); err != nil {
		t.Fatalf("BACKEND_BASE_URL fallback must satisfy the check: %v", err)
	}
	dev := prodOK()
	dev["APP_ENV"] = "dev"
	dev["EVENT_BASE_URL"] = "http://localhost:3000"
	if warns, err := Assert(MapEnv(dev)); err != nil || len(warns) != 0 {
		// вне production проверка EVENT_BASE_URL не выполняется вовсе
		t.Fatalf("dev: expected no error/warnings, got warns=%v err=%v", warns, err)
	}
}
