package rtb

// Диагностика отказов (metrics/rejects.go): какая стадия и какая группа
// таргетинга отсекли кандидата. Сценарии S1-S5 приёмки ТЗ «тематика и контекст».

import (
	"net/http"
	"strconv"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
)

func rejectStage(t *testing.T, reg *metrics.Registry, stage string) int {
	t.Helper()
	return atoiMetric(t, metricValue(t, reg, `bidder_candidate_reject_total{stage="`+stage+`"}`))
}

func rejectReason(t *testing.T, reg *metrics.Registry, reason string) int {
	t.Helper()
	return atoiMetric(t, metricValue(t, reg, `bidder_targeting_reject_total{reason="`+reason+`"}`))
}

func atoiMetric(t *testing.T, v string) int {
	t.Helper()
	n, err := strconv.Atoi(v)
	if err != nil {
		t.Fatalf("metric value %q is not an integer: %v", v, err)
	}
	return n
}

func iabSnapshot(cats ...string) *feed.Snapshot {
	snap := testSnapshot()
	snap.Candidates[0].T = &feed.Targeting{IABCategories: cats}
	return snap
}

func iabRequest(site string) string {
	return `{"id":"r1","at":1,"imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":1}],` +
		`"site":` + site + `,"device":{"ifa":"dev-1","ua":"Mozilla/5.0","geo":{"country":"KAZ"}}}`
}

func TestAcceptanceIABCategoriesSources(t *testing.T) {
	cases := []struct {
		name string
		site string
		want int
	}{
		{"S1 без категорий", `{"domain":"a.kz"}`, http.StatusNoContent},
		{"S2 site.cat", `{"domain":"a.kz","cat":["IAB1"]}`, http.StatusOK},
		{"S3 подкатегория IAB1-5", `{"domain":"a.kz","cat":["IAB1-5"]}`, http.StatusOK},
		{"S4 только pagecat", `{"domain":"a.kz","pagecat":["IAB2"]}`, http.StatusOK},
		{"S5 чужая категория", `{"domain":"a.kz","cat":["IAB9"]}`, http.StatusNoContent},
		{"sectioncat", `{"domain":"a.kz","sectioncat":["IAB4-2"]}`, http.StatusOK},
		{"content.cat", `{"domain":"a.kz","content":{"cat":["IAB2"]}}`, http.StatusOK},
		{"publisher.cat", `{"domain":"a.kz","publisher":{"id":"p","cat":["IAB1"]}}`, http.StatusOK},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := newTestHandler(iabSnapshot("IAB1", "IAB2", "IAB4"))
			if w := doBid(t, h, iabRequest(c.site)); w.Code != c.want {
				t.Fatalf("status = %d, want %d: %s", w.Code, c.want, w.Body.String())
			}
		})
	}
}

func TestAcceptanceIABBlockByParentAndChild(t *testing.T) {
	snap := func() *feed.Snapshot {
		s := testSnapshot()
		s.Candidates[0].T = &feed.Targeting{IABBlock: []string{"IAB1-5"}}
		return s
	}
	// S7: тот же код — отказ.
	if w := doBid(t, newTestHandler(snap()), iabRequest(`{"domain":"a.kz","cat":["IAB1-5"]}`)); w.Code != http.StatusNoContent {
		t.Fatalf("S7: want 204, got %d", w.Code)
	}
	// S8: родитель запроса блок-подкатегорию не задевает.
	if w := doBid(t, newTestHandler(snap()), iabRequest(`{"domain":"a.kz","cat":["IAB1"]}`)); w.Code != http.StatusOK {
		t.Fatalf("S8: want 200, got %d", w.Code)
	}
}

func TestRejectMetricsTargetingIAB(t *testing.T) {
	h := newTestHandler(iabSnapshot("IAB1", "IAB2", "IAB4"))
	reg := metrics.New()
	h.SetMetrics(reg)

	if w := doBid(t, h, iabRequest(`{"domain":"a.kz"}`)); w.Code != http.StatusNoContent {
		t.Fatalf("want 204, got %d", w.Code)
	}
	if got := rejectStage(t, reg, "targeting"); got != 1 {
		t.Fatalf(`stage="targeting" = %d, want 1`, got)
	}
	if got := rejectReason(t, reg, "inventory_iab"); got != 1 {
		t.Fatalf(`reason="inventory_iab" = %d, want 1`, got)
	}
	if got := metricValue(t, reg, `bidder_no_bid_total{reason="no_candidates"}`); got != "1" {
		t.Fatalf("no_candidates = %q, want 1", got)
	}
	// Успешный запрос счётчики отказов не двигает.
	if w := doBid(t, h, iabRequest(`{"domain":"a.kz","cat":["IAB1"]}`)); w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	if rejectStage(t, reg, "targeting") != 1 || rejectReason(t, reg, "inventory_iab") != 1 {
		t.Fatal("a successful bid must not change reject counters")
	}
}

// Сумма причин таргетинга всегда равна стадии targeting: каждый отказ считается один раз.
func TestRejectMetricsReasonsSumToTargetingStage(t *testing.T) {
	snap := testSnapshot()
	snap.Candidates[0].T = &feed.Targeting{GeoCountries: []string{"RU"}}
	h := newTestHandler(snap)
	reg := metrics.New()
	h.SetMetrics(reg)
	for i := 0; i < 3; i++ {
		doBid(t, h, iabRequest(`{"domain":"a.kz"}`)) // страна KAZ — отказ по geo
	}
	stage := rejectStage(t, reg, "targeting")
	sum := 0
	for _, name := range metrics.TargetingRejectReasons {
		sum += rejectReason(t, reg, name)
	}
	if stage != 3 || sum != stage {
		t.Fatalf("stage=%d, sum of reasons=%d, want both 3", stage, sum)
	}
	if rejectReason(t, reg, "geo") != 3 {
		t.Fatalf(`reason="geo" = %d, want 3`, rejectReason(t, reg, "geo"))
	}
}

func TestRejectMetricsOtherStages(t *testing.T) {
	type tc struct {
		name  string
		stage string
		snap  func() *feed.Snapshot
		body  string
	}
	okBody := iabRequest(`{"domain":"a.kz"}`)
	cases := []tc{
		{"type: размер не подходит", "type", testSnapshot,
			`{"id":"r","imp":[{"id":"1","banner":{"w":728,"h":90}}],"site":{"domain":"a.kz"},"device":{"ifa":"d"}}`},
		{"floor: imp.bidfloor выше цены", "floor", testSnapshot,
			`{"id":"r","imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":100000}],"site":{"domain":"a.kz"},"device":{"ifa":"d"}}`},
		{"ssp: партнёра нет в allowlist", "ssp", func() *feed.Snapshot {
			s := testSnapshot()
			s.Candidates[0].T = &feed.Targeting{SSPAllowlist: []string{"999"}}
			return s
		}, okBody},
		{"imp_targeting: secure_only", "imp_targeting", func() *feed.Snapshot {
			s := testSnapshot()
			s.Candidates[0].T = &feed.Targeting{SecureOnly: true}
			return s
		}, okBody},
		{"partner_type: тип запрещён партнёру", "partner_type", func() *feed.Snapshot {
			s := testSnapshot()
			s.Partners["key1"].AllowedTypes = map[string]bool{"video": true}
			return s
		}, okBody},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := newTestHandler(c.snap())
			reg := metrics.New()
			h.SetMetrics(reg)
			if w := doBid(t, h, c.body); w.Code != http.StatusNoContent {
				t.Fatalf("want 204, got %d: %s", w.Code, w.Body.String())
			}
			if got := rejectStage(t, reg, c.stage); got != 1 {
				t.Fatalf("stage=%q = %d, want 1", c.stage, got)
			}
			for _, other := range metrics.CandidateStages {
				if other != c.stage && rejectStage(t, reg, other) != 0 {
					t.Errorf("stage=%q must stay 0, got %d", other, rejectStage(t, reg, other))
				}
			}
		})
	}
}

func TestRejectMetricsBudgetGate(t *testing.T) {
	snap := testSnapshot()
	snap.Candidates[0].CampBudget = 0.0001 // дневной бюджет меньше цены ставки: гейт откажет
	h, _ := newMiniRedisBidHandler(t, snap)
	reg := metrics.New()
	h.SetMetrics(reg)
	body := `{"id":"r1","imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":1}],` +
		`"device":{"ifa":"dev-1","ua":"Mozilla/5.0"},"site":{"domain":"pub.example"}}`
	if w := doBid(t, h, body); w.Code != http.StatusNoContent {
		t.Fatalf("want 204, got %d: %s", w.Code, w.Body.String())
	}
	if got := rejectStage(t, reg, "budget_gate"); got != 1 {
		t.Fatalf(`stage="budget_gate" = %d, want 1`, got)
	}
}

// Метки только из фиксированных наборов, серии есть сразу (нулевые значения видны).
func TestRejectMetricsExposition(t *testing.T) {
	reg := metrics.New()
	wantStages := []string{"type", "partner_type", "ssp", "targeting", "geo_v2", "imp_targeting", "floor", "budget_gate", "audience", "fx", "adm"}
	if len(metrics.CandidateStages) != len(wantStages) {
		t.Fatalf("stages = %v", metrics.CandidateStages)
	}
	for i, s := range wantStages {
		if metrics.CandidateStages[i] != s {
			t.Errorf("stage #%d = %q, want %q", i, metrics.CandidateStages[i], s)
		}
		if metricValue(t, reg, `bidder_candidate_reject_total{stage="`+s+`"}`) != "0" {
			t.Errorf("stage %q must be exported as 0 before any reject", s)
		}
	}
	for _, r := range metrics.TargetingRejectReasons {
		if metricValue(t, reg, `bidder_targeting_reject_total{reason="`+r+`"}`) != "0" {
			t.Errorf("reason %q must be exported as 0 before any reject", r)
		}
	}
}
