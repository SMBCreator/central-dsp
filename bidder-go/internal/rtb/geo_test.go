package rtb

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/geo"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
)

func f64(v float64) *float64 { return &v }

func geoTestIndex() *geo.Index {
	return geo.NewIndex(&geo.Payload{
		Version: "test:0:0",
		Places: []geo.Place{
			{ID: 2017370, CC: "RU", Type: "country", Names: []string{"россия", "russia"}},
			{ID: 1522867, CC: "KZ", Type: "country", Names: []string{"казахстан", "kazakhstan"}},
			{ID: 542415, CC: "RU", Type: "region", Parent: 2017370, RegionCode: "RU-KDA", Names: []string{"краснодарский край", "krasnodar krai"}},
			{ID: 524894, CC: "RU", Type: "region", Parent: 2017370, RegionCode: "RU-MOW", Names: []string{"москва", "moscow"}},
			{ID: 491422, CC: "RU", Type: "city", Parent: 542415, Names: []string{"сочи", "sochi"}, Lat: f64(43.59699), Lon: f64(39.72477), Pop: 327608},
			{ID: 542420, CC: "RU", Type: "city", Parent: 542415, Names: []string{"краснодар", "krasnodar"}, Lat: f64(45.04484), Lon: f64(38.97603), Pop: 744933},
		},
	})
}

// geoSnapshot — кампания с GEO v2: Краснодарский край без Сочи.
func geoSnapshot(unknown string, withIndex bool) *feed.Snapshot {
	snap := testSnapshot()
	c := &snap.Candidates[0]
	c.Targeting = json.RawMessage(`{"geo_place_ids":[542415],"geo_exclude_place_ids":[491422],"geo_unknown":"` + unknown + `"}`)
	c.T = feed.ParseTargeting(c.Targeting)
	c.Geo = geo.NewSets(c.T.GeoPlaceIDs, c.T.GeoExcludePlaceIDs, c.T.GeoUnknown)
	snap.HasGeoV2 = true
	if withIndex {
		snap.Geo, snap.GeoVersion = geoTestIndex(), "test:0:0"
		c.Geo.Bind(snap.Geo)
	}
	return snap
}

func geoBody(geoJSON string) string {
	return `{"id":"r1","device":{"geo":` + geoJSON + `},"imp":[{"id":"1","banner":{"w":300,"h":250}}]}`
}

func TestGeoV2IncludeExclude(t *testing.T) {
	h := newTestHandler(geoSnapshot("strict", true))
	cases := []struct {
		name, geo string
		want      int
	}{
		{"city inside included region", `{"country":"RUS","region":"Краснодарский край","city":"Краснодар"}`, http.StatusOK},
		{"city without region resolves via parent", `{"country":"RUS","city":"krasnodar"}`, http.StatusOK},
		{"region only", `{"country":"RU","region":"RU-KDA"}`, http.StatusOK},
		{"excluded city", `{"country":"RUS","region":"Краснодарский край","city":"Сочи"}`, http.StatusNoContent},
		{"other region", `{"country":"RUS","region":"Москва"}`, http.StatusNoContent},
		{"strict: country only is unknown for region targeting", `{"country":"RUS"}`, http.StatusNoContent},
		{"strict: no geo", `{}`, http.StatusNoContent},
	}
	for _, tc := range cases {
		if w := doBid(t, h, geoBody(tc.geo)); w.Code != tc.want {
			t.Errorf("%s: want %d, got %d (%s)", tc.name, tc.want, w.Code, w.Body.String())
		}
	}
}

func TestGeoV2LenientUnknown(t *testing.T) {
	h := newTestHandler(geoSnapshot("lenient", true))
	if w := doBid(t, h, geoBody(`{}`)); w.Code != http.StatusOK {
		t.Fatalf("lenient: no geo must bid, got %d", w.Code)
	}
	// Только страна (город не найден): Россия содержит Краснодарский край.
	if w := doBid(t, h, geoBody(`{"country":"RUS","city":"Неизвестноград"}`)); w.Code != http.StatusOK {
		t.Fatalf("lenient: country containing target must bid, got %d", w.Code)
	}
	if w := doBid(t, h, geoBody(`{"country":"KAZ"}`)); w.Code != http.StatusNoContent {
		t.Fatalf("lenient: unrelated country must not bid, got %d", w.Code)
	}
	// Известное место вне таргетинга lenient не спасает.
	if w := doBid(t, h, geoBody(`{"country":"RUS","region":"Москва"}`)); w.Code != http.StatusNoContent {
		t.Fatalf("lenient must still reject known other region, got %d", w.Code)
	}
}

// Флаг backend выключен (geo_version = null -> нет индекса): v2-группа не
// крутится вовсе, а не «везде» (fail-closed, как в rtb.py).
func TestGeoV2FailClosedWithoutDataset(t *testing.T) {
	h := newTestHandler(geoSnapshot("lenient", false))
	if w := doBid(t, h, geoBody(`{"country":"RUS","region":"Краснодарский край"}`)); w.Code != http.StatusNoContent {
		t.Fatalf("without geo dataset v2 group must not bid, got %d", w.Code)
	}
}

func TestGeoV2LatLonOption(t *testing.T) {
	h := newTestHandler(geoSnapshot("strict", true))
	body := geoBody(`{"country":"RUS","lat":45.05,"lon":38.98}`)
	if w := doBid(t, h, body); w.Code != http.StatusNoContent {
		t.Fatalf("latlon off: country-only must be unknown, got %d", w.Code)
	}
	h.SetGeoOptions(geo.Options{LatLon: true, RadiusKM: 25})
	if w := doBid(t, h, body); w.Code != http.StatusOK {
		t.Fatalf("latlon on: nearest Krasnodar must bid, got %d", w.Code)
	}
}

func TestGeoV2MetricsAndUnmatched(t *testing.T) {
	h := newTestHandler(geoSnapshot("strict", true))
	reg := metrics.New()
	h.SetMetrics(reg)
	h.SetInternalToken("secret")
	doBid(t, h, geoBody(`{"country":"RUS","region":"Krasnodarskiy","city":"Krasnodar"}`))
	doBid(t, h, geoBody(`{"country":"RUS","region":"Krasnodarskiy"}`))

	mw := httptest.NewRecorder()
	reg.Handler().ServeHTTP(mw, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	out := mw.Body.String()
	for _, want := range []string{`bidder_geo_resolve_total{level="city"} 1`, `bidder_geo_resolve_total{level="country"} 1`} {
		if !strings.Contains(out, want) {
			t.Errorf("metrics: missing %q", want)
		}
	}

	r := httptest.NewRequest(http.MethodGet, "/internal/geo/unmatched?limit=10", nil)
	w := httptest.NewRecorder()
	h.GeoUnmatched(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("unmatched without token: want 403, got %d", w.Code)
	}
	r.Header.Set(internalCheckHeader, "secret")
	w = httptest.NewRecorder()
	h.GeoUnmatched(w, r)
	var resp struct {
		Items []geo.UnmatchedValue `json:"items"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if len(resp.Items) != 1 || resp.Items[0].Kind != "region" || resp.Items[0].Value != "Krasnodarskiy" || resp.Items[0].Count != 2 {
		t.Fatalf("unmatched: got %+v", resp.Items)
	}
}

// Без v2-кампаний гео не резолвится вовсе (нулевая цена для hot path).
func TestGeoV2SkippedWithoutV2Campaigns(t *testing.T) {
	snap := testSnapshot()
	snap.Geo, snap.GeoVersion = geoTestIndex(), "test:0:0"
	h := newTestHandler(snap)
	reg := metrics.New()
	h.SetMetrics(reg)
	if w := doBid(t, h, geoBody(`{"country":"RUS","city":"Сочи"}`)); w.Code != http.StatusOK {
		t.Fatalf("legacy campaign must bid, got %d", w.Code)
	}
	mw := httptest.NewRecorder()
	reg.Handler().ServeHTTP(mw, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if strings.Contains(mw.Body.String(), `bidder_geo_resolve_total{`) {
		t.Fatal("geo must not be resolved when no campaign uses v2 geo")
	}
}
