package metrics

import (
	"strings"
	"sync"
	"testing"
)

func TestRejectTallyCountsStageAndReason(t *testing.T) {
	var tally RejectTally
	tally.Stage(StageFloor)
	tally.Stage(StageFloor)
	tally.Targeting(0) // geo
	tally.Targeting(2) // inventory_iab

	if got := tally.StageCount(StageFloor); got != 2 {
		t.Errorf("floor: %d, want 2", got)
	}
	if got := tally.StageCount(StageTargeting); got != 2 {
		t.Errorf("targeting: %d, want 2 (каждый Targeting считает и стадию)", got)
	}
	if tally.ReasonCount(0) != 1 || tally.ReasonCount(2) != 1 {
		t.Errorf("reasons: geo=%d inventory_iab=%d, want 1 и 1", tally.ReasonCount(0), tally.ReasonCount(2))
	}
}

func TestRejectTallyIgnoresOutOfRange(t *testing.T) {
	var tally RejectTally
	tally.Stage(-1)
	tally.Stage(NumStages)
	for i := 0; i < NumStages; i++ {
		if tally.StageCount(i) != 0 {
			t.Fatalf("стадия %d изменилась от неверного индекса", i)
		}
	}
	// Неверный индекс причины сворачивается в «other», метка не выходит за набор.
	tally.Targeting(-5)
	tally.Targeting(NumTargetReasons + 7)
	if got := tally.ReasonCount(NumTargetReasons - 1); got != 2 {
		t.Errorf("other: %d, want 2", got)
	}
}

func TestRejectTallyNilSafe(t *testing.T) {
	var tally *RejectTally
	tally.Stage(StageType)
	tally.Targeting(0)
	var reg *Registry
	reg.AddRejects(&RejectTally{})
	New().AddRejects(nil)
}

func TestAddRejectsExposition(t *testing.T) {
	reg := New()
	var tally RejectTally
	tally.Stage(StageType)
	tally.Stage(StagePartnerType)
	tally.Targeting(4) // inventory_other
	reg.AddRejects(&tally)
	reg.AddRejects(&tally)

	body := scrape(t, reg)

	for _, want := range []string{
		`bidder_candidate_reject_total{stage="type"} 2`,
		`bidder_candidate_reject_total{stage="partner_type"} 2`,
		`bidder_candidate_reject_total{stage="targeting"} 2`,
		`bidder_candidate_reject_total{stage="adm"} 0`,
		`bidder_targeting_reject_total{reason="inventory_other"} 2`,
		`bidder_targeting_reject_total{reason="geo"} 0`,
		"# TYPE bidder_candidate_reject_total counter",
		"# TYPE bidder_targeting_reject_total counter",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("в /metrics нет %q", want)
		}
	}
	if n := strings.Count(body, "bidder_candidate_reject_total{"); n != NumStages {
		t.Errorf("серий stage: %d, want %d", n, NumStages)
	}
	if n := strings.Count(body, "bidder_targeting_reject_total{"); n != NumTargetReasons {
		t.Errorf("серий reason: %d, want %d", n, NumTargetReasons)
	}
}

func TestAddRejectsConcurrent(t *testing.T) {
	reg := New()
	var wg sync.WaitGroup
	for g := 0; g < 16; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 500; i++ {
				var tally RejectTally
				tally.Stage(StageFloor)
				tally.Targeting(1)
				reg.AddRejects(&tally)
			}
		}()
	}
	wg.Wait()

	body := scrape(t, reg)
	for _, want := range []string{
		`bidder_candidate_reject_total{stage="floor"} 8000`,
		`bidder_candidate_reject_total{stage="targeting"} 8000`,
		`bidder_targeting_reject_total{reason="device"} 8000`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("в /metrics нет %q", want)
		}
	}
}
