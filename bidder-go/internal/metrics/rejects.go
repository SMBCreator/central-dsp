package metrics

// Диагностика отказов кандидатов: no_bid_total{reason="no_candidates"} не говорит,
// какой фильтр отсёк кандидатов. Здесь две серии с фиксированными метками:
//
//	bidder_candidate_reject_total{stage}  — на какой стадии bidOnImp отброшен кандидат;
//	bidder_targeting_reject_total{reason} — какая группа проверок таргетинга (Match) отсекла.
//
// Горячий путь не трогает общие атомики на каждую проверку: bidOnImp копит
// отказы в локальном RejectTally и сбрасывает его одним AddRejects на imp.

import (
	"fmt"
	"net/http"
	"sync/atomic"
)

// Стадии отказа кандидата (метка stage). Порядок — порядок проверок в bidOnImp.
const (
	StageType         = iota // тип/размер креатива не подходит слоту; несовместимость видео-слота
	StagePartnerType         // тип рекламы запрещён партнёру (считается один раз на imp: отброшены все кандидаты)
	StageSSP                 // ssp_allowlist кандидата не содержит партнёра
	StageTargeting           // таргетинг line item, badv/bcat, блоки оптимизатора, глобальные списки
	StageGeoV2               // GEO v2
	StageImpTargeting        // таргетинг уровня imp, private auction без сделки
	StageFloor               // цена ниже floor
	StageBudgetGate          // отказ или ошибка бюджетного гейта
	StageAudience            // ретаргетинг-сегменты
	StageFX                  // нет курса ЦБ для валюты floor/ответа
	StageAdm                 // разметку креатива не собрать безопасно (adm / native / VAST)
	NumStages
)

// CandidateStages — значения метки stage; индекс = константа Stage*.
var CandidateStages = [NumStages]string{
	"type", "partner_type", "ssp", "targeting", "geo_v2", "imp_targeting",
	"floor", "budget_gate", "audience", "fx", "adm",
}

// NumTargetReasons — число причин отказа таргетинга.
const NumTargetReasons = 11

// TargetingRejectReasons — значения метки reason. Порядок совпадает с
// targeting.Reason (1-based): индекс = int(reason) - 1 (проверяется тестом в rtb).
var TargetingRejectReasons = [NumTargetReasons]string{
	"geo", "device", "inventory_iab", "inventory_domain", "inventory_other",
	"audience", "time", "keywords", "context", "regulatory", "other",
}

// RejectTally копит отказы одного вызова bidOnImp; методы nil-safe.
type RejectTally struct {
	stage  [NumStages]int64
	reason [NumTargetReasons]int64
}

// Stage — кандидат отброшен на стадии s.
func (t *RejectTally) Stage(s int) {
	if t != nil && s >= 0 && s < NumStages {
		t.stage[s]++
	}
}

// Targeting — кандидат отброшен таргетингом по причине с индексом reasonIdx
// (0-based, см. TargetingRejectReasons); считается и стадия StageTargeting.
func (t *RejectTally) Targeting(reasonIdx int) {
	if t == nil {
		return
	}
	t.stage[StageTargeting]++
	if reasonIdx < 0 || reasonIdx >= NumTargetReasons {
		reasonIdx = NumTargetReasons - 1 // other
	}
	t.reason[reasonIdx]++
}

// StageCount / ReasonCount — накопленные значения (для тестов).
func (t *RejectTally) StageCount(s int) int64  { return t.stage[s] }
func (t *RejectTally) ReasonCount(i int) int64 { return t.reason[i] }

type rejectCounters struct {
	stage  [NumStages]atomic.Int64
	reason [NumTargetReasons]atomic.Int64
}

// AddRejects сбрасывает накопленные отказы в реестр: по одному атомарному
// сложению на ненулевую серию.
func (r *Registry) AddRejects(t *RejectTally) {
	if r == nil || t == nil {
		return
	}
	for i, n := range t.stage {
		if n != 0 {
			r.rejects.stage[i].Add(n)
		}
	}
	for i, n := range t.reason {
		if n != 0 {
			r.rejects.reason[i].Add(n)
		}
	}
}

func (r *Registry) writeRejects(w http.ResponseWriter) {
	fmt.Fprintf(w, "# HELP bidder_candidate_reject_total Candidates dropped per bid-request stage (partner_type counts impressions: all candidates are dropped at once). Does not add up to no_bid_total.\n# TYPE bidder_candidate_reject_total counter\n")
	for i, name := range CandidateStages {
		fmt.Fprintf(w, "bidder_candidate_reject_total{stage=%q} %d\n", name, r.rejects.stage[i].Load())
	}
	fmt.Fprintf(w, "# HELP bidder_targeting_reject_total Candidates dropped by line item targeting, by check group (equals stage=\"targeting\" of bidder_candidate_reject_total).\n# TYPE bidder_targeting_reject_total counter\n")
	for i, name := range TargetingRejectReasons {
		fmt.Fprintf(w, "bidder_targeting_reject_total{reason=%q} %d\n", name, r.rejects.reason[i].Load())
	}
}
