package pctr

// Эталонные значения посчитаны backend/ml/pctr.py (_logit/_sigmoid) на тех
// же сегментах — тест фиксирует побитовый паритет predict() с Python.

import (
	"math"
	"testing"
)

func refModel() *Model {
	return FromFeed(&FeedModel{
		Version:   7,
		GlobalCTR: 0.01,
		Segments: []Segment{
			{Type: "domain", Key: "pub.example", CTR: 0.03},
			{Type: "creative", Key: "cr-1", CTR: 0.005},
			{Type: "device_type", Key: "mobile", CTR: 0.02},
			{Type: "hour", Key: "14", CTR: 0.012},
		},
	})
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-12 }

func TestPredict_MatchesPythonReference(t *testing.T) {
	m := refModel()
	cases := []struct {
		name           string
		domain, cr, dt string
		hour           int
		wantPCTR       float64
		wantMatched    int
	}{
		{"all four segments, domain case-insensitive", "PUB.example", "cr-1", "mobile", 14, 0.0360323597141241, 4},
		{"domain only", "pub.example", "cr-x", "desktop", 3, 0.03, 1},
		{"no segment -> prior", "other", "cr-x", "tv", -1, 0.01, 0},
		{"creative only, empty keys skipped", "", "cr-1", "", -1, 0.005, 1},
	}
	for _, c := range cases {
		p := m.Predict(c.domain, c.cr, c.dt, c.hour)
		if p.Matched != c.wantMatched || !near(p.PCTR, c.wantPCTR) || p.Baseline != 0.01 || p.Version != 7 {
			t.Errorf("%s: got %+v, want pctr=%v matched=%d", c.name, p, c.wantPCTR, c.wantMatched)
		}
	}
}

func TestMultiplier_Guardrails(t *testing.T) {
	m := refModel()
	// 0.03/0.01 = 3.0 -> clamp 1.3
	if mult, ok := m.Predict("pub.example", "", "", -1).Multiplier(); !ok || mult != MultMax {
		t.Fatalf("domain-only: mult=%v ok=%v, want %v", mult, ok, MultMax)
	}
	// 0.005/0.01 = 0.5 -> clamp 0.7
	if mult, ok := m.Predict("", "cr-1", "", -1).Multiplier(); !ok || mult != MultMin {
		t.Fatalf("creative-only: mult=%v ok=%v, want %v", mult, ok, MultMin)
	}
	// 0.012/0.01 = 1.2 -> внутри коридора
	if mult, ok := m.Predict("", "", "", 14).Multiplier(); !ok || !near(mult, 1.2) {
		t.Fatalf("hour-only: mult=%v ok=%v, want 1.2", mult, ok)
	}
	// Ни одного сегмента -> модель не влияет (Python: matched == 0 -> цена базовая).
	if mult, ok := m.Predict("zzz", "", "", -1).Multiplier(); ok || mult != 1 {
		t.Fatalf("prior-only: mult=%v ok=%v, want 1/false", mult, ok)
	}
}

func TestFromFeed_InvalidModelIsNil(t *testing.T) {
	if FromFeed(nil) != nil {
		t.Fatal("nil feed model must give nil")
	}
	for _, g := range []float64{0, 1, -0.1, 1.5} {
		if FromFeed(&FeedModel{GlobalCTR: g}) != nil {
			t.Fatalf("global_ctr=%v must be rejected like _load_active_model", g)
		}
	}
	var m *Model
	if p := m.Predict("a", "b", "c", 1); p.Matched != 0 || p.PCTR != 0 {
		t.Fatalf("nil model Predict must be zero value, got %+v", p)
	}
}
