package rtb

// Паритет Go и Python-эталона на общих векторах: тот же JSON читает
// backend/tests/test_ctx_match.py. Запрос проходит весь путь биддера:
// BidRequest -> buildRequestView, targeting JSON -> feed.ParseTargeting ->
// feedTargetingToMatch -> targeting.MatchReason.

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/targeting"
)

const matchCasesPath = "../targeting/testdata/match_cases.json"

type vectorFile struct {
	RequestCats []struct {
		Name string          `json:"name"`
		Req  json.RawMessage `json:"req"`
		Want []string        `json:"want"`
	} `json:"request_cats"`
	Match []matchVector `json:"match"`
}

type matchVector struct {
	Name      string          `json:"name"`
	Targeting json.RawMessage `json:"targeting"`
	Req       json.RawMessage `json:"req"`
	Want      bool            `json:"want"`
	Reason    string          `json:"reason"`
}

func loadVectors(t *testing.T) vectorFile {
	t.Helper()
	raw, err := os.ReadFile(matchCasesPath)
	if err != nil {
		t.Fatal(err)
	}
	var vf vectorFile
	if err := json.Unmarshal(raw, &vf); err != nil {
		t.Fatal(err)
	}
	return vf
}

func mustBidRequest(t *testing.T, raw json.RawMessage) *BidRequest {
	t.Helper()
	var req BidRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		t.Fatalf("unmarshal request %s: %v", raw, err)
	}
	return &req
}

func TestVectorsRequestCats(t *testing.T) {
	vf := loadVectors(t)
	if len(vf.RequestCats) < 10 {
		t.Fatalf("too few request_cats vectors: %d", len(vf.RequestCats))
	}
	for _, c := range vf.RequestCats {
		got := requestCats(mustBidRequest(t, c.Req))
		if len(got) == 0 && len(c.Want) == 0 {
			continue
		}
		if !reflect.DeepEqual(got, c.Want) {
			t.Errorf("%s: requestCats = %#v, want %#v", c.Name, got, c.Want)
		}
	}
}

func TestVectorsMatch(t *testing.T) {
	vf := loadVectors(t)
	if len(vf.Match) < 20 {
		t.Fatalf("too few match vectors: %d", len(vf.Match))
	}
	for _, c := range vf.Match {
		view := buildRequestView(mustBidRequest(t, c.Req))
		tg := feedTargetingToMatch(feed.ParseTargeting(c.Targeting))
		reason := targeting.MatchReason(tg, view)
		if got := reason == targeting.ReasonNone; got != c.Want {
			t.Errorf("%s: pass = %v (reason %q), want %v", c.Name, got, reason.String(), c.Want)
		}
		if reason.String() != c.Reason {
			t.Errorf("%s: reason = %q, want %q", c.Name, reason.String(), c.Reason)
		}
		if targeting.Match(tg, view) != c.Want {
			t.Errorf("%s: Match disagrees with MatchReason", c.Name)
		}
	}
}

// Новые поля категорий не должны ломать разбор запроса даже при нестандартных типах.
func TestCatListTolerantDecoding(t *testing.T) {
	bodies := []string{
		`{"site":{"pagecat":"IAB2"}}`,
		`{"site":{"pagecat":[1,2,3]}}`,
		`{"site":{"pagecat":{"a":1}}}`,
		`{"site":{"sectioncat":null,"publisher":{"cat":5}}}`,
		`{"app":{"pagecat":[["IAB1"]],"publisher":{"cat":true}}}`,
	}
	for _, b := range bodies {
		var req BidRequest
		if err := json.Unmarshal([]byte(b), &req); err != nil {
			t.Errorf("%s: request must decode, got %v", b, err)
		}
	}
}

// targeting.Reason идёт с единицы в том же порядке, что metrics.TargetingRejectReasons.
func TestReasonOrderMatchesMetricLabels(t *testing.T) {
	if int(targeting.ReasonCount)-1 != metrics.NumTargetReasons {
		t.Fatalf("ReasonCount-1 = %d, metrics.NumTargetReasons = %d", int(targeting.ReasonCount)-1, metrics.NumTargetReasons)
	}
	for r := targeting.Reason(1); r < targeting.ReasonCount; r++ {
		if got, want := metrics.TargetingRejectReasons[reasonIdx(r)], r.String(); got != want {
			t.Errorf("metrics label #%d = %q, targeting.Reason = %q", reasonIdx(r), got, want)
		}
	}
}

// bcat запроса и таргетинг площадки применяют одно правило совпадения кодов.
func TestIABBlockedSharesTargetingRule(t *testing.T) {
	cases := []struct {
		bcat, cats []string
		want       bool
	}{
		{[]string{"IAB7"}, []string{"IAB7-3"}, true},
		{[]string{"IAB7-3"}, []string{"IAB7-3"}, true},
		{[]string{"IAB7-3"}, []string{"IAB7"}, false},
		{[]string{"iab7"}, []string{"IAB7"}, true},
		{[]string{"IAB7"}, []string{"IAB70"}, false},
		{[]string{""}, []string{"IAB7"}, false},
		{nil, []string{"IAB7"}, false},
	}
	for _, c := range cases {
		if got := iabBlocked(c.bcat, c.cats); got != c.want {
			t.Errorf("iabBlocked(%v, %v) = %v, want %v", c.bcat, c.cats, got, c.want)
		}
	}
}
