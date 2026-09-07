// Package pctr — порт backend/ml/pctr.py::predict (audit R-03, Часть B).
//
// Сегментная Beta-сглаженная модель с лог-odds комбинацией:
//
//	logit(pctr) = logit(g) + sum_seg(logit(ctr_seg) - logit(g))
//
// где g — глобальный CTR модели, ctr_seg — сглаженный CTR найденного сегмента
// (domain / creative / device_type / hour). Обучение остаётся в Python-кроне;
// сюда модель приезжает фидом (/rtb-feed/campaigns -> pctr_model), поэтому
// predict не делает сетевых вызовов и не может упасть.
package pctr

import (
	"math"
	"strconv"
	"strings"
)

// Segment — строка ml_pctr_segments в фиде.
type Segment struct {
	Type string  `json:"type"`
	Key  string  `json:"key"`
	CTR  float64 `json:"ctr"`
}

// FeedModel — pctr_model из фида (nil в JSON = модели нет).
type FeedModel struct {
	Version   int       `json:"version"`
	GlobalCTR float64   `json:"global_ctr"`
	Segments  []Segment `json:"segments"`
}

// Model — модель в форме для hot path.
type Model struct {
	Version   int
	GlobalCTR float64
	segments  map[segKey]float64
}

type segKey struct{ typ, key string }

// Prediction — результат predict; Matched == 0 означает чистый приор
// (в Python тогда множитель не применяется).
type Prediction struct {
	PCTR     float64
	Baseline float64
	Version  int
	Matched  int
}

// Guardrails value-based биддинга — те же числа, что в rtb.py.
const (
	MultMin = 0.7
	MultMax = 1.3
)

// FromFeed строит модель; nil, если фид её не отдал или global_ctr вне (0,1)
// (та же валидация, что _load_active_model).
func FromFeed(fm *FeedModel) *Model {
	if fm == nil || fm.GlobalCTR <= 0 || fm.GlobalCTR >= 1 {
		return nil
	}
	m := &Model{Version: fm.Version, GlobalCTR: fm.GlobalCTR, segments: make(map[segKey]float64, len(fm.Segments))}
	for _, s := range fm.Segments {
		m.segments[segKey{s.Type, s.Key}] = s.CTR
	}
	return m
}

// Predict — pCTR для комбинации фич. domain приводится к нижнему регистру,
// hour < 0 = неизвестен; пустые ключи пропускаются (как None в Python).
func (m *Model) Predict(domain, creativeID, deviceType string, hour int) Prediction {
	if m == nil {
		return Prediction{}
	}
	g := m.GlobalCTR
	x := logit(g)
	matched := 0
	hourKey := ""
	if hour >= 0 {
		hourKey = strconv.Itoa(hour)
	}
	for _, f := range [...]struct{ typ, key string }{
		{"domain", strings.ToLower(domain)},
		{"creative", creativeID},
		{"device_type", deviceType},
		{"hour", hourKey},
	} {
		if f.key == "" {
			continue
		}
		if ctr, ok := m.segments[segKey{f.typ, f.key}]; ok {
			x += logit(ctr) - logit(g)
			matched++
		}
	}
	p := g
	if matched > 0 {
		p = sigmoid(x)
	}
	return Prediction{PCTR: p, Baseline: g, Version: m.Version, Matched: matched}
}

// Multiplier — pctr/baseline, зажатый в [MultMin, MultMax]; 1 и false, если
// модель не должна влиять на цену (нет совпавших сегментов / нет baseline).
func (p Prediction) Multiplier() (float64, bool) {
	if p.Matched == 0 || p.Baseline <= 0 {
		return 1, false
	}
	return math.Min(MultMax, math.Max(MultMin, p.PCTR/p.Baseline)), true
}

func logit(p float64) float64 {
	p = math.Min(math.Max(p, 1e-6), 1-1e-6)
	return math.Log(p / (1 - p))
}

func sigmoid(x float64) float64 {
	if x >= 0 {
		return 1 / (1 + math.Exp(-x))
	}
	z := math.Exp(x)
	return z / (1 + z)
}
