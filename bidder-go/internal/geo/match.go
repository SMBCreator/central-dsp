package geo

import (
	"sort"
	"sync"
)

// Sets — предвычисленный при рефреше фида GEO v2-таргетинг группы:
// O(1) проверка каждого звена цепочки (не больше maxChain lookup'ов).
type Sets struct {
	include map[int64]struct{}
	exclude map[int64]struct{}
	lenient bool
	within  map[int64]struct{} // предки мест включения (Bind), только lenient
}

// NewSets — nil, если у группы нет v2-гео (нет ограничения).
func NewSets(include, exclude []int, unknown string) *Sets {
	if len(include) == 0 && len(exclude) == 0 {
		return nil
	}
	s := &Sets{lenient: unknown == "lenient"}
	if len(include) > 0 {
		s.include = make(map[int64]struct{}, len(include))
		for _, id := range include {
			s.include[int64(id)] = struct{}{}
		}
	}
	if len(exclude) > 0 {
		s.exclude = make(map[int64]struct{}, len(exclude))
		for _, id := range exclude {
			s.exclude[int64(id)] = struct{}{}
		}
	}
	return s
}

// Match — порт geo_v2.match_geo_v2: исключения важнее включений; включение
// срабатывает по любому звену цепочки; unknown — по режиму strict/lenient.
func (s *Sets) Match(r *Resolved) bool {
	if s == nil {
		return true
	}
	if r == nil || r.Level == LevelUnknown {
		return s.lenient
	}
	ids := r.IDs()
	if s.exclude != nil {
		for _, id := range ids {
			if _, ok := s.exclude[id]; ok {
				return false
			}
		}
	}
	if s.include != nil {
		for _, id := range ids {
			if _, ok := s.include[id]; ok {
				return true
			}
		}
		if s.within != nil && r.Level != LevelCity && r.N > 0 {
			_, ok := s.within[r.Chain[0]]
			return ok
		}
		return false
	}
	return true
}

// Bind — для lenient: предки мест включения. Гео, определённое не до конца
// (только страна RU), проходит, если содержит выбранное место (Краснодарский
// край). Вызывается при рефреше фида после загрузки справочника.
func (s *Sets) Bind(idx *Index) {
	if s == nil || !s.lenient || s.include == nil || idx == nil {
		return
	}
	s.within = make(map[int64]struct{}, len(s.include))
	for id := range s.include {
		var r Resolved
		idx.chain(id, &r)
		for _, a := range r.IDs() {
			if a != id {
				s.within[a] = struct{}{}
			}
		}
	}
}

// Unmatched — топ значений device.geo.region/city, которые не удалось
// сопоставить (по партнёрам): источник для geo_ssp_alias. Ограничен по числу
// ключей — значения приходят от SSP и не должны раздувать память.
type Unmatched struct {
	mu     sync.Mutex
	counts map[unmatchedKey]int64
	limit  int
}

type unmatchedKey struct{ Partner, Kind, Value string }

// UnmatchedValue — строка отчёта GET /internal/geo/unmatched.
type UnmatchedValue struct {
	Partner string `json:"partner"`
	Kind    string `json:"kind"`
	Value   string `json:"value"`
	Count   int64  `json:"count"`
}

func NewUnmatched(limit int) *Unmatched {
	return &Unmatched{counts: map[unmatchedKey]int64{}, limit: limit}
}

const maxUnmatchedValueLen = 120

// Add — учитывает значение; новые ключи сверх limit отбрасываются (старые
// продолжают считаться), чтобы мусорный трафик не вытеснял частые значения.
func (u *Unmatched) Add(partner, kind, value string) {
	if u == nil || value == "" {
		return
	}
	if len(value) > maxUnmatchedValueLen {
		value = value[:maxUnmatchedValueLen]
	}
	k := unmatchedKey{partner, kind, value}
	u.mu.Lock()
	if _, ok := u.counts[k]; ok || len(u.counts) < u.limit {
		u.counts[k]++
	}
	u.mu.Unlock()
}

// Top — n самых частых значений.
func (u *Unmatched) Top(n int) []UnmatchedValue {
	if u == nil {
		return nil
	}
	u.mu.Lock()
	out := make([]UnmatchedValue, 0, len(u.counts))
	for k, c := range u.counts {
		out = append(out, UnmatchedValue{k.Partner, k.Kind, k.Value, c})
	}
	u.mu.Unlock()
	sort.Slice(out, func(i, j int) bool {
		if out[i].Count != out[j].Count {
			return out[i].Count > out[j].Count
		}
		return out[i].Partner+out[i].Kind+out[i].Value < out[j].Partner+out[j].Kind+out[j].Value
	})
	if len(out) > n {
		out = out[:n]
	}
	return out
}
