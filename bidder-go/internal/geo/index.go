package geo

import (
	"math"
	"sort"
	"strings"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/targeting"
)

// Level — глубина, до которой удалось сопоставить гео запроса (bounded label метрик).
type Level string

const (
	LevelCity    Level = "city"
	LevelRegion  Level = "region"
	LevelCountry Level = "country"
	LevelUnknown Level = "unknown"
)

// Levels — все значения для инициализации серий метрик.
var Levels = []Level{LevelCity, LevelRegion, LevelCountry, LevelUnknown}

const (
	latLonCellDeg = 0.5
	// DefaultLatLonRadiusKM — GEO_LATLON_RADIUS_KM по умолчанию.
	DefaultLatLonRadiusKM = 25.0
	// primaryNames — первые синонимы места = нормализованные name_ru, name_en
	// (ops/geo_import.py); у регионов они важнее синонимов соседа.
	primaryNames = 2
	maxChain     = 4
)

// Place / Alias / Payload — формат GET /rtb-feed/geo (backend/geo_v2.feed_payload).
type Place struct {
	ID         int64    `json:"id"`
	CC         string   `json:"cc"`
	Type       string   `json:"t"`
	Parent     int64    `json:"p"`
	RegionCode string   `json:"rc"`
	Names      []string `json:"n"`
	Lat        *float64 `json:"lat"`
	Lon        *float64 `json:"lon"`
	Pop        int64    `json:"pop"`
}

type Alias struct {
	PartnerID string `json:"partner_id"`
	Kind      string `json:"kind"` // region | city
	ValueNorm string `json:"value_norm"`
	PlaceID   int64  `json:"place_id"`
}

type Payload struct {
	Version string  `json:"version"`
	Places  []Place `json:"places"`
	Aliases []Alias `json:"aliases"`
}

type node struct {
	cc     string
	parent int64
	lat    float64
	lon    float64
	hasLL  bool
	pop    int64
}

type cell struct{ i, j int }

type ccName struct{ cc, name string }

type aliasKey struct{ partner, kind, value string }

type regionRank struct {
	tier int
	id   int64
}

// Index — иммутабельный индекс справочника; строится при рефреше фида,
// в hot path только map lookup.
type Index struct {
	Version    string
	nodes      map[int64]node
	country    map[string]int64
	regionCode map[string]int64
	regionName map[ccName]int64
	cityName   map[ccName][]int64
	alias      map[aliasKey]int64
	grid       map[cell][]int64
}

func cellOf(lat, lon float64) cell {
	return cell{int(math.Floor(lat / latLonCellDeg)), int(math.Floor(lon / latLonCellDeg))}
}

// NewIndex — порт geo_v2.GeoIndex.__init__.
func NewIndex(p *Payload) *Index {
	idx := &Index{
		Version:    p.Version,
		nodes:      make(map[int64]node, len(p.Places)),
		country:    map[string]int64{},
		regionCode: map[string]int64{},
		regionName: map[ccName]int64{},
		cityName:   map[ccName][]int64{},
		alias:      make(map[aliasKey]int64, len(p.Aliases)),
		grid:       map[cell][]int64{},
	}
	ranks := map[ccName]regionRank{}
	for i := range p.Places {
		pl := &p.Places[i]
		n := node{cc: pl.CC, parent: pl.Parent, pop: pl.Pop}
		if pl.Lat != nil && pl.Lon != nil {
			n.lat, n.lon, n.hasLL = *pl.Lat, *pl.Lon, true
		}
		idx.nodes[pl.ID] = n
		switch pl.Type {
		case "country":
			idx.country[pl.CC] = pl.ID
		case "region":
			if pl.RegionCode != "" {
				idx.regionCode[strings.ToUpper(pl.RegionCode)] = pl.ID
			}
			for j, name := range pl.Names {
				key := ccName{pl.CC, name}
				tier := 1
				if j < primaryNames {
					tier = 0
				}
				r := regionRank{tier, pl.ID}
				if cur, ok := ranks[key]; !ok || r.tier < cur.tier || (r.tier == cur.tier && r.id < cur.id) {
					ranks[key] = r
					idx.regionName[key] = pl.ID
				}
			}
		case "city", "town":
			for _, name := range pl.Names {
				key := ccName{pl.CC, name}
				idx.cityName[key] = append(idx.cityName[key], pl.ID)
			}
			if n.hasLL {
				c := cellOf(n.lat, n.lon)
				idx.grid[c] = append(idx.grid[c], pl.ID)
			}
		}
	}
	for _, ids := range idx.cityName {
		sort.Slice(ids, func(a, b int) bool {
			pa, pb := idx.nodes[ids[a]].pop, idx.nodes[ids[b]].pop
			if pa != pb {
				return pa > pb
			}
			return ids[a] < ids[b]
		})
	}
	for _, a := range p.Aliases {
		if _, ok := idx.nodes[a.PlaceID]; ok {
			idx.alias[aliasKey{a.PartnerID, a.Kind, a.ValueNorm}] = a.PlaceID
		}
	}
	return idx
}

// Len — число мест (для логов/метрик).
func (idx *Index) Len() int {
	if idx == nil {
		return 0
	}
	return len(idx.nodes)
}

// Resolved — результат сопоставления: цепочка «место -> родители до страны».
type Resolved struct {
	Chain           [maxChain]int64
	N               int
	Level           Level
	UnmatchedRegion string
	UnmatchedCity   string
}

// IDs — цепочка срезом (без аллокации: смотрит в массив).
func (r *Resolved) IDs() []int64 { return r.Chain[:r.N] }

func (idx *Index) chain(id int64, out *Resolved) {
	out.N = 0
	cur := id
	for cur != 0 && out.N < maxChain {
		n, ok := idx.nodes[cur]
		if !ok {
			break
		}
		out.Chain[out.N] = cur
		out.N++
		cur = n.parent
	}
}

func (idx *Index) region(partner, cc, raw string) int64 {
	norm := Normalize(raw)
	if norm == "" {
		return 0
	}
	if id, ok := idx.alias[aliasKey{partner, "region", norm}]; ok {
		return id
	}
	code := strings.ToUpper(strings.TrimSpace(raw))
	if !strings.Contains(code, "-") && cc != "" {
		code = cc + "-" + code
	}
	if id, ok := idx.regionCode[code]; ok && (cc == "" || idx.nodes[id].cc == cc) {
		return id
	}
	if cc == "" {
		return 0
	}
	return idx.regionName[ccName{cc, norm}]
}

func (idx *Index) city(partner, cc, raw string, regionID int64) int64 {
	norm := Normalize(raw)
	if norm == "" {
		return 0
	}
	if id, ok := idx.alias[aliasKey{partner, "city", norm}]; ok {
		return id
	}
	if cc == "" {
		return 0
	}
	cands := idx.cityName[ccName{cc, norm}]
	if len(cands) == 0 {
		return 0
	}
	if regionID != 0 {
		for _, id := range cands {
			if idx.nodes[id].parent == regionID {
				return id
			}
		}
	}
	return cands[0]
}

func haversineKM(lat1, lon1, lat2, lon2 float64) float64 {
	p1, p2 := lat1*math.Pi/180, lat2*math.Pi/180
	dp, dl := p2-p1, (lon2-lon1)*math.Pi/180
	a := math.Sin(dp/2)*math.Sin(dp/2) + math.Cos(p1)*math.Cos(p2)*math.Sin(dl/2)*math.Sin(dl/2)
	return 2 * 6371.0088 * math.Asin(math.Min(1, math.Sqrt(a)))
}

func (idx *Index) nearest(cc string, lat, lon, radiusKM float64) int64 {
	var best int64
	bestD, bestPop := math.Inf(1), int64(0)
	c := cellOf(lat, lon)
	for di := -1; di <= 1; di++ {
		for dj := -1; dj <= 1; dj++ {
			for _, id := range idx.grid[cell{c.i + di, c.j + dj}] {
				n := idx.nodes[id]
				if cc != "" && n.cc != cc {
					continue
				}
				d := math.Round(haversineKM(lat, lon, n.lat, n.lon)*1e6) / 1e6
				if d > radiusKM {
					continue
				}
				if best == 0 || d < bestD || (d == bestD && (n.pop > bestPop || (n.pop == bestPop && id < best))) {
					best, bestD, bestPop = id, d, n.pop
				}
			}
		}
	}
	return best
}

// Query — гео bid request'а (device.geo) + партнёр (для алиасов SSP).
type Query struct {
	PartnerID string
	Country   string
	Region    string
	City      string
	Lat, Lon  float64
	HasLatLon bool
}

// Options — флаги сопоставления (GEO_LATLON_ENABLED, GEO_LATLON_RADIUS_KM).
type Options struct {
	LatLon   bool
	RadiusKM float64
}

// Resolve — порт geo_v2.GeoIndex.resolve. nil-индекс (справочник не
// загружен) = unknown без «непонятых значений»: это не вина SSP.
func (idx *Index) Resolve(q Query, o Options) Resolved {
	var out Resolved
	out.Level = LevelUnknown
	if idx == nil {
		return out
	}
	cc := targeting.NormalizeCountry(q.Country)
	countryID := idx.country[cc]
	var regionID, cityID int64
	if q.Region != "" {
		regionID = idx.region(q.PartnerID, cc, q.Region)
	}
	if regionID != 0 && cc == "" {
		cc = idx.nodes[regionID].cc
		countryID = idx.country[cc]
	}
	if q.City != "" {
		cityID = idx.city(q.PartnerID, cc, q.City, regionID)
	}
	if cityID == 0 && o.LatLon && q.HasLatLon && !(q.Lat == 0 && q.Lon == 0) {
		r := o.RadiusKM
		if r <= 0 {
			r = DefaultLatLonRadiusKM
		}
		cityID = idx.nearest(cc, q.Lat, q.Lon, r)
	}
	if q.Region != "" && regionID == 0 {
		out.UnmatchedRegion = q.Region
	}
	if q.City != "" && cityID == 0 {
		out.UnmatchedCity = q.City
	}
	switch {
	case cityID != 0:
		idx.chain(cityID, &out)
		out.Level = LevelCity
		out.UnmatchedCity = ""
	case regionID != 0:
		idx.chain(regionID, &out)
		out.Level = LevelRegion
		out.UnmatchedRegion = ""
	case countryID != 0:
		out.Chain[0], out.N = countryID, 1
		out.Level = LevelCountry
	}
	return out
}
