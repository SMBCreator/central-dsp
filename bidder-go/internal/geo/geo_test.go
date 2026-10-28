package geo

import (
	"encoding/json"
	"os"
	"testing"
)

// Кейсы общие с backend/tests/test_geo_v2.py: расхождение Go и Python-эталона
// валит оба набора тестов.
type fixtures struct {
	Normalize [][2]string `json:"normalize"`
	Places    []Place     `json:"places"`
	Aliases   []Alias     `json:"aliases"`
	Resolve   []struct {
		Name            string   `json:"name"`
		Partner         string   `json:"partner"`
		Country         string   `json:"country"`
		Region          string   `json:"region"`
		City            string   `json:"city"`
		Lat             *float64 `json:"lat"`
		Lon             *float64 `json:"lon"`
		LatLon          bool     `json:"latlon"`
		WantChain       []int64  `json:"want_chain"`
		WantLevel       Level    `json:"want_level"`
		WantUnmatchedRe string   `json:"want_unmatched_region"`
		WantUnmatchedCi string   `json:"want_unmatched_city"`
	} `json:"resolve"`
	Match []struct {
		Name      string `json:"name"`
		Targeting struct {
			Include []int  `json:"geo_place_ids"`
			Exclude []int  `json:"geo_exclude_place_ids"`
			Unknown string `json:"geo_unknown"`
		} `json:"targeting"`
		Chain []int64 `json:"chain"`
		Level Level   `json:"level"`
		Want  bool    `json:"want"`
	} `json:"match"`
}

func load(t testing.TB) *fixtures {
	t.Helper()
	raw, err := os.ReadFile("testdata/fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	var f fixtures
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	return &f
}

func TestNormalizeParity(t *testing.T) {
	for _, c := range load(t).Normalize {
		if got := Normalize(c[0]); got != c[1] {
			t.Errorf("Normalize(%q) = %q, want %q", c[0], got, c[1])
		}
	}
}

func TestResolveParity(t *testing.T) {
	f := load(t)
	idx := NewIndex(&Payload{Version: "fixtures", Places: f.Places, Aliases: f.Aliases})
	for _, c := range f.Resolve {
		t.Run(c.Name, func(t *testing.T) {
			q := Query{PartnerID: c.Partner, Country: c.Country, Region: c.Region, City: c.City}
			if c.Lat != nil && c.Lon != nil {
				q.Lat, q.Lon, q.HasLatLon = *c.Lat, *c.Lon, true
			}
			got := idx.Resolve(q, Options{LatLon: c.LatLon, RadiusKM: DefaultLatLonRadiusKM})
			ids := got.IDs()
			if len(ids) != len(c.WantChain) {
				t.Fatalf("chain = %v, want %v", ids, c.WantChain)
			}
			for i := range ids {
				if ids[i] != c.WantChain[i] {
					t.Fatalf("chain = %v, want %v", ids, c.WantChain)
				}
			}
			if got.Level != c.WantLevel {
				t.Errorf("level = %s, want %s", got.Level, c.WantLevel)
			}
			if got.UnmatchedRegion != c.WantUnmatchedRe || got.UnmatchedCity != c.WantUnmatchedCi {
				t.Errorf("unmatched = (%q, %q), want (%q, %q)", got.UnmatchedRegion, got.UnmatchedCity, c.WantUnmatchedRe, c.WantUnmatchedCi)
			}
		})
	}
}

func TestMatchParity(t *testing.T) {
	f := load(t)
	idx := NewIndex(&Payload{Places: f.Places, Aliases: f.Aliases})
	for _, c := range f.Match {
		t.Run(c.Name, func(t *testing.T) {
			r := Resolved{Level: c.Level}
			r.N = copy(r.Chain[:], c.Chain)
			s := NewSets(c.Targeting.Include, c.Targeting.Exclude, c.Targeting.Unknown)
			s.Bind(idx)
			if got := s.Match(&r); got != c.Want {
				t.Fatalf("Match = %v, want %v", got, c.Want)
			}
		})
	}
}

func TestNilIndexIsUnknownWithoutUnmatched(t *testing.T) {
	var idx *Index
	r := idx.Resolve(Query{Country: "RUS", City: "Moscow"}, Options{})
	if r.Level != LevelUnknown || r.N != 0 || r.UnmatchedCity != "" {
		t.Fatalf("nil index: %+v", r)
	}
}

func TestUnmatchedIsBounded(t *testing.T) {
	u := NewUnmatched(2)
	u.Add("a", "city", "x")
	u.Add("a", "city", "y")
	u.Add("a", "city", "z") // сверх лимита: новый ключ отброшен
	u.Add("a", "city", "x")
	top := u.Top(10)
	if len(top) != 2 || top[0].Value != "x" || top[0].Count != 2 {
		t.Fatalf("top = %+v", top)
	}
}

func BenchmarkResolveCity(b *testing.B) {
	f := load(b)
	idx := NewIndex(&Payload{Places: f.Places, Aliases: f.Aliases})
	q := Query{PartnerID: "p", Country: "RUS", Region: "Краснодарский край", City: "Сочи"}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = idx.Resolve(q, Options{})
	}
}

func BenchmarkMatchSets(b *testing.B) {
	s := NewSets([]int{542415, 524901, 551487}, []int{524894}, "")
	r := Resolved{Level: LevelCity, N: 3, Chain: [maxChain]int64{491422, 542415, 2017370}}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = s.Match(&r)
	}
}
