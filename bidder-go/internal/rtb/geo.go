package rtb

import (
	"encoding/json"
	"net/http"
	"os"
	"strconv"
	"strings"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/geo"
)

// unmatchedGeoLimit — максимум различных несопоставленных значений в памяти.
const unmatchedGeoLimit = 5000

// GeoOptionsFromEnv — GEO_LATLON_ENABLED / GEO_LATLON_RADIUS_KM (как geo_v2.py).
func GeoOptionsFromEnv() geo.Options {
	o := geo.Options{RadiusKM: geo.DefaultLatLonRadiusKM}
	switch strings.ToLower(strings.TrimSpace(os.Getenv("GEO_LATLON_ENABLED"))) {
	case "1", "true", "yes", "on":
		o.LatLon = true
	}
	if v, err := strconv.ParseFloat(strings.TrimSpace(os.Getenv("GEO_LATLON_RADIUS_KM")), 64); err == nil && v > 0 {
		o.RadiusKM = v
	}
	return o
}

// SetGeoOptions задаёт флаги сопоставления гео (вызывается из main).
func (h *Handler) SetGeoOptions(o geo.Options) { h.geoOpts = o }

// resolveGeo — гео запроса по справочнику, один раз на запрос. nil — ни у
// одного кандидата нет v2-гео (работа не нужна) или флаг backend выключен
// (тогда v2-группы отсеиваются в matchGeoV2).
func (h *Handler) resolveGeo(req *BidRequest, partner *feed.Partner, snap *feed.Snapshot) *geo.Resolved {
	if !snap.HasGeoV2 || snap.GeoVersion == "" {
		return nil
	}
	q := geo.Query{PartnerID: partner.ID}
	if d := req.Device; d != nil && d.Geo != nil {
		g := d.Geo
		q.Country, q.Region, q.City = g.Country, g.Region, g.City
		q.Lat, q.Lon = g.Lat, g.Lon
		q.HasLatLon = g.Lat != 0 || g.Lon != 0
	}
	r := snap.Geo.Resolve(q, h.geoOpts)
	h.metrics.IncGeoResolve(string(r.Level))
	if h.geoUnmatched != nil {
		h.geoUnmatched.Add(partner.ID, "region", r.UnmatchedRegion)
		h.geoUnmatched.Add(partner.ID, "city", r.UnmatchedCity)
	}
	return &r
}

// matchGeoV2 — проверка GEO v2 кандидата (AND со старыми geo_* ключами).
// Флаг backend выключен (нет версии справочника) — v2-группа не матчится:
// fail-closed, как в rtb.py, чтобы не крутить «везде» вместо выбранного гео.
func matchGeoV2(c *feed.Candidate, snap *feed.Snapshot, r *geo.Resolved) bool {
	if c.Geo == nil {
		return true
	}
	if snap.GeoVersion == "" || r == nil {
		return false
	}
	return c.Geo.Match(r)
}

// GeoUnmatched — GET /internal/geo/unmatched?limit=N: самые частые значения
// region/city от SSP, не найденные в справочнике (источник для алиасов).
// Только с internal-токеном, как /internal/feed/reload.
func (h *Handler) GeoUnmatched(w http.ResponseWriter, r *http.Request) {
	if h.internalToken == "" {
		http.NotFound(w, r)
		return
	}
	if !h.isInternalCheck(r) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	n := 100
	if v, err := strconv.Atoi(r.URL.Query().Get("limit")); err == nil && v > 0 && v <= 1000 {
		n = v
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"items": h.geoUnmatched.Top(n)})
}
