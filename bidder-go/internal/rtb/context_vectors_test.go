package rtb

// Матрица «тематика × слова × режим × опция без тематики × стоп-условия» на
// общих векторах testdata/context_cases.json (их же читает
// backend/tests/test_context_modes.py). Путь запроса — как в боевом коде:
// BidRequest -> buildRequestView, targeting JSON -> feed.ParseTargeting ->
// feedTargetingToMatch -> targeting.MatchReason.

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/targeting"
)

func TestContextVectors(t *testing.T) {
	raw, err := os.ReadFile("../targeting/testdata/context_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Match []matchVector `json:"match"`
	}
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	if len(f.Match) < 300 {
		t.Fatalf("too few context vectors: %d", len(f.Match))
	}
	for _, c := range f.Match {
		view := buildRequestView(mustBidRequest(t, c.Req))
		// Снапшот готовит слова при загрузке; здесь — тот же путь через feed.ParseTargeting.
		ft := feed.ParseTargeting(c.Targeting)
		ft.Keywords = targeting.NewPreparedKeywords(ft.KeywordsMatch, ft.KeywordsAllow, ft.KeywordsBlock)
		reason := targeting.MatchReason(feedTargetingToMatch(ft), view)
		if got := reason == targeting.ReasonNone; got != c.Want {
			t.Errorf("%s: pass = %v (reason %q), want %v", c.Name, got, reason.String(), c.Want)
			continue
		}
		if reason.String() != c.Reason {
			t.Errorf("%s: reason = %q, want %q", c.Name, reason.String(), c.Reason)
		}
	}
}

// Новые ключи доходят из JSON фида до матчера (сквозная цепочка).
func TestFeedCarriesContextKeys(t *testing.T) {
	ft := feed.ParseTargeting([]byte(`{"iab_allow_unknown":true,"keywords_match":"word_start","context_mode":"any","keywords_allow":["ипотек"]}`))
	if !ft.IABAllowUnknown || ft.KeywordsMatch != "word_start" || ft.ContextMode != "any" {
		t.Fatalf("feed.Targeting lost context keys: %+v", ft)
	}
	m := feedTargetingToMatch(ft)
	if !m.IABAllowUnknown || m.KeywordsMatch != "word_start" || m.ContextMode != "any" {
		t.Fatalf("targeting.Targeting lost context keys: %+v", m)
	}
}
