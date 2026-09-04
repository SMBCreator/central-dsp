package rtb

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/targeting"
)

// bcat / battr фильтры и bid.cat / bid.attr в ответе (миграция 035).

func TestBCatBlocksCreativeByTier1Parent(t *testing.T) {
	snap := testSnapshot()
	snap.Candidates[0].IABCategories = []string{"IAB7-3"} // Health & Fitness > Alternative medicine
	h := newTestHandler(snap)
	body := `{"id":"r1","bcat":["IAB7"],"imp":[{"id":"1","banner":{"w":300,"h":250}}]}`
	if w := doBid(t, h, body); w.Code != http.StatusNoContent {
		t.Fatalf("bcat tier-1 parent must block: want 204, got %d", w.Code)
	}
	// Точный код тоже блокирует.
	body = `{"id":"r2","bcat":["IAB7-3"],"imp":[{"id":"1","banner":{"w":300,"h":250}}]}`
	if w := doBid(t, h, body); w.Code != http.StatusNoContent {
		t.Fatalf("bcat exact: want 204, got %d", w.Code)
	}
	// Соседняя категория — не блокирует; в ответе bid.cat.
	body = `{"id":"r3","bcat":["IAB8"],"imp":[{"id":"1","banner":{"w":300,"h":250}}]}`
	w := doBid(t, h, body)
	if w.Code != http.StatusOK {
		t.Fatalf("unrelated bcat: want 200, got %d", w.Code)
	}
	var resp BidResponse
	_ = json.Unmarshal(w.Body.Bytes(), &resp)
	if len(resp.SeatBid) == 0 || len(resp.SeatBid[0].Bid) == 0 || len(resp.SeatBid[0].Bid[0].Cat) != 1 || resp.SeatBid[0].Bid[0].Cat[0] != "IAB7-3" {
		t.Fatalf("bid.cat must echo creative categories, got %s", w.Body.String())
	}
}

func TestBCatIgnoredWhenCreativeHasNoCategories(t *testing.T) {
	h := newTestHandler(testSnapshot()) // IABCategories пусто
	body := `{"id":"r1","bcat":["IAB7","IAB25"],"imp":[{"id":"1","banner":{"w":300,"h":250}}]}`
	if w := doBid(t, h, body); w.Code != http.StatusOK {
		t.Fatalf("no declared cats must not be blocked: want 200, got %d", w.Code)
	}
}

func TestBAttrBlocksCreativeAttribute(t *testing.T) {
	snap := testSnapshot()
	snap.Candidates[0].Attrs = []int{6, 13} // 6 = in-banner video auto-play, 13 = user interactive
	h := newTestHandler(snap)
	body := `{"id":"r1","imp":[{"id":"1","banner":{"w":300,"h":250,"battr":[6]}}]}`
	if w := doBid(t, h, body); w.Code != http.StatusNoContent {
		t.Fatalf("battr must block: want 204, got %d", w.Code)
	}
	body = `{"id":"r2","imp":[{"id":"1","banner":{"w":300,"h":250,"battr":[1,2]}}]}`
	w := doBid(t, h, body)
	if w.Code != http.StatusOK {
		t.Fatalf("non-overlapping battr: want 200, got %d", w.Code)
	}
	var resp BidResponse
	_ = json.Unmarshal(w.Body.Bytes(), &resp)
	if got := resp.SeatBid[0].Bid[0].Attr; len(got) != 2 || got[0] != 6 || got[1] != 13 {
		t.Fatalf("bid.attr must echo creative attrs, got %v", got)
	}
}

// Новые ключи таргетинга: device_makes, schain_required, viewability_min,
// video_placements, video_protocols — должны доезжать из feed.Targeting.

func TestFeedTargetingMapsNewKeys(t *testing.T) {
	ft := feed.ParseTargeting(json.RawMessage(`{
		"device_makes":["Apple","samsung"],"schain_required":true,
		"viewability_min":70,"video_placements":[1,2],"video_protocols":[3,7]}`))
	m := feedTargetingToMatch(ft)
	if len(m.DeviceMakes) != 2 || !m.SChainRequired {
		t.Fatalf("request-level keys not mapped: %+v", m)
	}
	im := feedImpTargeting(ft)
	if im.ViewabilityMin != 70 || len(im.VideoPlacements) != 2 || len(im.VideoProtocols) != 2 {
		t.Fatalf("imp-level keys not mapped: %+v", im)
	}
}

func TestDeviceMakeTargeting(t *testing.T) {
	snap := testSnapshot()
	snap.Candidates[0].T = feed.ParseTargeting(json.RawMessage(`{"device_makes":["Apple"]}`))
	h := newTestHandler(snap)
	imp := `"imp":[{"id":"1","banner":{"w":300,"h":250}}]`
	if w := doBid(t, h, `{"id":"r1","device":{"make":"apple","ua":"x"},`+imp+`}`); w.Code != http.StatusOK {
		t.Fatalf("make apple (case-insensitive): want 200, got %d", w.Code)
	}
	if w := doBid(t, h, `{"id":"r2","device":{"make":"Xiaomi","ua":"x"},`+imp+`}`); w.Code != http.StatusNoContent {
		t.Fatalf("make xiaomi: want 204, got %d", w.Code)
	}
	if w := doBid(t, h, `{"id":"r3","device":{"ua":"x"},`+imp+`}`); w.Code != http.StatusNoContent {
		t.Fatalf("no make with filter: want 204, got %d", w.Code)
	}
}

func TestSChainRequired(t *testing.T) {
	snap := testSnapshot()
	snap.Candidates[0].T = feed.ParseTargeting(json.RawMessage(`{"schain_required":true}`))
	h := newTestHandler(snap)
	imp := `"imp":[{"id":"1","banner":{"w":300,"h":250}}]`
	cases := []struct {
		name, src string
		want      int
	}{
		{"no source", ``, http.StatusNoContent},
		{"incomplete", `,"source":{"schain":{"complete":0,"nodes":[{"asi":"ssp.ru","sid":"1","hp":1}]}}`, http.StatusNoContent},
		{"complete no nodes", `,"source":{"schain":{"complete":1,"nodes":[]}}`, http.StatusNoContent},
		{"complete 2.6", `,"source":{"schain":{"complete":1,"nodes":[{"asi":"ssp.ru","sid":"1","hp":1}]}}`, http.StatusOK},
		{"complete 2.5 ext", `,"source":{"ext":{"schain":{"complete":1,"nodes":[{"asi":"ssp.ru","sid":"1","hp":1}]}}}`, http.StatusOK},
	}
	for _, c := range cases {
		if w := doBid(t, h, `{"id":"r","`+`x":0,`+imp+c.src+`}`); w.Code != c.want {
			t.Errorf("%s: want %d, got %d", c.name, c.want, w.Code)
		}
	}
}

func TestViewabilityAndVideoImpSignals(t *testing.T) {
	// Прямые проверки MatchImp/buildImpView, без всего конвейера.
	imp := &Imp{ID: "1", Video: &Video{MaxDuration: 30, Plcmt: 1, Protocols: []int{2, 3}},
		Metric: []Metric{{Type: "viewability", Value: 0.62, Vendor: "ias"}}}
	v := buildImpView(imp)
	if v.Viewability != 62 || v.VideoPlacement != 1 || len(v.VideoProtocols) != 2 {
		t.Fatalf("buildImpView: %+v", v)
	}
	if !targeting.MatchImp(&targeting.ImpTargeting{ViewabilityMin: 60}, v) {
		t.Fatal("62% >= 60% must pass")
	}
	if targeting.MatchImp(&targeting.ImpTargeting{ViewabilityMin: 70}, v) {
		t.Fatal("62% < 70% must fail")
	}
	// Нет метрики + задан фильтр = мимо.
	noMetric := buildImpView(&Imp{ID: "1", Banner: &Banner{W: 300, H: 250}})
	if noMetric.Viewability != -1 || targeting.MatchImp(&targeting.ImpTargeting{ViewabilityMin: 10}, noMetric) {
		t.Fatal("missing viewability metric with filter must fail")
	}
	// plcmt / protocols.
	if !targeting.MatchImp(&targeting.ImpTargeting{VideoPlacements: []int{1}}, v) {
		t.Fatal("plcmt 1 in [1]")
	}
	if targeting.MatchImp(&targeting.ImpTargeting{VideoPlacements: []int{2, 3}}, v) {
		t.Fatal("plcmt 1 not in [2,3]")
	}
	if !targeting.MatchImp(&targeting.ImpTargeting{VideoProtocols: []int{3, 7}}, v) {
		t.Fatal("protocols intersect on 3")
	}
	if targeting.MatchImp(&targeting.ImpTargeting{VideoProtocols: []int{7}}, v) {
		t.Fatal("protocols [7] vs [2,3] no intersection")
	}
	// SSP не передал protocols — принимаем.
	legacy := buildImpView(&Imp{ID: "1", Video: &Video{Placement: 4}})
	if legacy.VideoPlacement != 4 {
		t.Fatal("placement 2.5 fallback")
	}
	if !targeting.MatchImp(&targeting.ImpTargeting{VideoProtocols: []int{7}}, legacy) {
		t.Fatal("empty SSP protocols must pass")
	}
	// Некоторые SSP шлют viewability в процентах.
	pct := buildImpView(&Imp{ID: "1", Metric: []Metric{{Type: "viewability", Value: 55}}})
	if pct.Viewability != 55 {
		t.Fatalf("percent-style viewability: got %d", pct.Viewability)
	}
}
