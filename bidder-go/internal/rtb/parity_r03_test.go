package rtb

// Audit R-03 — исполняемая parity matrix «настройка кабинета -> BidResponse».
// Каждый тест: один и тот же BidRequest даёт бид на базовом снапшоте и no-bid
// (или другую цену) после включения настройки. Истина — backend/routers/rtb.py.
// Строки матрицы: docs/audit/parity_matrix.md (раздел R-03).

import (
	"encoding/json"
	"math"
	"net/http"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/pctr"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/prefilter"
)

const parityReq = `{"id":"p1","at":1,"imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":1}],` +
	`"site":{"domain":"pub.example","publisher":{"id":"pub-9"}},` +
	`"device":{"ua":"Mozilla/5.0 (Linux; Android 13) Chrome/120","os":"android","devicetype":1,` +
	`"ifa":"dev-1","geo":{"country":"RUS"}}}`

// bidsOn — 200/204 для snapshot по parityReq.
func bidsOn(t *testing.T, snap *feed.Snapshot) bool {
	t.Helper()
	w := doBid(t, newTestHandler(snap), parityReq)
	switch w.Code {
	case http.StatusOK:
		return true
	case http.StatusNoContent:
		return false
	}
	t.Fatalf("unexpected status %d: %s", w.Code, w.Body.String())
	return false
}

func withOptBlock(snap *feed.Snapshot, blockType, value string) *feed.Snapshot {
	camp := snap.Candidates[0].CampaignID
	if snap.OptBlocks == nil {
		snap.OptBlocks = map[string]map[string]map[string]bool{}
	}
	if snap.OptBlocks[camp] == nil {
		snap.OptBlocks[camp] = map[string]map[string]bool{}
	}
	if snap.OptBlocks[camp][blockType] == nil {
		snap.OptBlocks[camp][blockType] = map[string]bool{}
	}
	snap.OptBlocks[camp][blockType][strings.ToLower(value)] = true
	return snap
}

func TestParity_BaselineBids(t *testing.T) {
	if !bidsOn(t, testSnapshot()) {
		t.Fatal("baseline snapshot must bid on parityReq")
	}
}

// Optimizer: каждое из десяти измерений блокирует ровно свой запрос и не
// трогает чужой (регистр значения не важен — Python делает .lower()).
func TestParity_OptimizerBlocksEveryDimension(t *testing.T) {
	hour := strconv.Itoa(time.Now().UTC().Hour())
	otherHour := strconv.Itoa((time.Now().UTC().Hour() + 12) % 24)
	cases := []struct {
		blockType, hit, miss string
	}{
		{"domain", "PUB.example", "other.example"},
		{"supply_partner", "5", "6"},
		{"device_type", "mobile", "desktop"},
		{"geo_country", "rus", "usa"},
		{"os", "android", "ios"},
		{"browser", "chrome", "safari"},
		{"ad_type", "banner", "video"},
		{"creative", "21", "22"},
		{"line_item", "11", "12"},
		{"hour", hour, otherHour},
	}
	for _, tc := range cases {
		if bidsOn(t, withOptBlock(testSnapshot(), tc.blockType, tc.hit)) {
			t.Errorf("optimizer block %s=%q must suppress the bid", tc.blockType, tc.hit)
		}
		if !bidsOn(t, withOptBlock(testSnapshot(), tc.blockType, tc.miss)) {
			t.Errorf("optimizer block %s=%q must not affect an unrelated request", tc.blockType, tc.miss)
		}
	}
}

// Блок другой кампании не задевает нашу.
func TestParity_OptimizerBlockIsPerCampaign(t *testing.T) {
	snap := testSnapshot()
	snap.OptBlocks = map[string]map[string]map[string]bool{
		"other-camp": {"domain": {"pub.example": true}},
	}
	if !bidsOn(t, snap) {
		t.Fatal("block of another campaign must not suppress our bid")
	}
}

// Clean traffic: тумблер кампании + домен в списке «грязных». Оба условия
// обязательны — без тумблера грязный домен покупается, без списка тумблер
// ничего не режет (fail-open, как в Python).
func TestParity_CleanTrafficOnly(t *testing.T) {
	dirty := map[string]bool{"pub.example": true}

	snap := testSnapshot()
	snap.DirtyDomains = dirty
	if !bidsOn(t, snap) {
		t.Fatal("clean_traffic_only=false: dirty domain is still purchased")
	}

	snap = testSnapshot()
	snap.Candidates[0].CleanTrafficOnly = true
	if !bidsOn(t, snap) {
		t.Fatal("clean_traffic_only=true without a dirty list must fail-open")
	}

	snap = testSnapshot()
	snap.Candidates[0].CleanTrafficOnly = true
	snap.DirtyDomains = dirty
	if bidsOn(t, snap) {
		t.Fatal("clean_traffic_only=true on a dirty domain must be no-bid")
	}

	snap = testSnapshot()
	snap.Candidates[0].CleanTrafficOnly = true
	snap.DirtyDomains = map[string]bool{"elsewhere.example": true}
	if !bidsOn(t, snap) {
		t.Fatal("dirty list without our domain must not block")
	}
}

// Scoring-профиль (enforce): домен в scoring_blocklist кампании -> no-bid;
// та же запись у другой кампании нас не касается.
func TestParity_ScoringBlocklist(t *testing.T) {
	snap := testSnapshot()
	snap.ScoringBlocks = map[string]map[string]bool{"1": {"pub.example": true}}
	if bidsOn(t, snap) {
		t.Fatal("scoring blocklist domain must be no-bid")
	}
	snap = testSnapshot()
	snap.ScoringBlocks = map[string]map[string]bool{"other": {"pub.example": true}}
	if !bidsOn(t, snap) {
		t.Fatal("scoring blocklist of another campaign must not block")
	}
}

// pCTR value-based биддинг (Часть B, порт rtb.py): цена умножается на
// clamp(pctr/baseline, 0.7, 1.3) только для objective traffic/conversions на
// CPM; floor и max_bid соблюдаются; без модели/совпадений — базовая цена.
func TestParity_PCTRMultiplier(t *testing.T) {
	priceFor := func(t *testing.T, snap *feed.Snapshot, req string) float64 {
		t.Helper()
		w := doBid(t, newTestHandler(snap), req)
		if w.Code != http.StatusOK {
			t.Fatalf("want bid, got %d: %s", w.Code, w.Body.String())
		}
		var resp BidResponse
		if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
			t.Fatal(err)
		}
		return resp.SeatBid[0].Bid[0].Price
	}
	// Модель с одним доменным сегментом (pub.example — домен parityReq):
	// ctr/0.01 = множитель до клампа.
	model := func(ctr float64) *pctr.Model {
		return pctr.FromFeed(&pctr.FeedModel{Version: 1, GlobalCTR: 0.01,
			Segments: []pctr.Segment{{Type: "domain", Key: "pub.example", CTR: ctr}}})
	}
	base := priceFor(t, testSnapshot(), parityReq)

	t.Run("no model -> base price", func(t *testing.T) {
		snap := testSnapshot()
		snap.Candidates[0].Objective = "traffic"
		if got := priceFor(t, snap, parityReq); got != base {
			t.Fatalf("without model price must stay %v, got %v", base, got)
		}
	})
	t.Run("objective clicks/ctr/empty -> model ignored", func(t *testing.T) {
		for _, obj := range []string{"", "clicks", "ctr"} {
			snap := testSnapshot()
			snap.Candidates[0].Objective = obj
			snap.PCTR = model(0.03)
			if got := priceFor(t, snap, parityReq); got != base {
				t.Fatalf("objective %q must not use pCTR: want %v, got %v", obj, base, got)
			}
		}
	})
	t.Run("cpc line item -> model ignored", func(t *testing.T) {
		snap := testSnapshot()
		snap.Candidates[0].Objective = "traffic"
		snap.Candidates[0].BidType = "cpc"
		snap.PCTR = model(0.03)
		if got := priceFor(t, snap, parityReq); got != base {
			t.Fatalf("cpc must not use pCTR: want %v, got %v", base, got)
		}
	})
	t.Run("traffic+cpm -> clamped up 1.3", func(t *testing.T) {
		snap := testSnapshot()
		snap.Candidates[0].Objective = "traffic"
		snap.PCTR = model(0.03)
		want := roundPrice(base * pctr.MultMax)
		if got := priceFor(t, snap, parityReq); math.Abs(got-want) > 1e-6 {
			t.Fatalf("want %v (x1.3), got %v", want, got)
		}
	})
	t.Run("conversions+cpm -> clamped down 0.7", func(t *testing.T) {
		snap := testSnapshot()
		snap.Candidates[0].Objective = "conversions"
		snap.PCTR = model(0.002)
		want := roundPrice(base * pctr.MultMin)
		if got := priceFor(t, snap, parityReq); math.Abs(got-want) > 1e-6 {
			t.Fatalf("want %v (x0.7), got %v", want, got)
		}
	})
	t.Run("in-range multiplier 1.2", func(t *testing.T) {
		snap := testSnapshot()
		snap.Candidates[0].Objective = "traffic"
		snap.PCTR = model(0.012)
		want := roundPrice(base * 1.2)
		if got := priceFor(t, snap, parityReq); math.Abs(got-want) > 1e-6 {
			t.Fatalf("want %v (x1.2), got %v", want, got)
		}
	})
	t.Run("max_bid caps the boosted price", func(t *testing.T) {
		snap := testSnapshot()
		snap.Candidates[0].Objective = "traffic"
		capPrice := base * 1.1
		snap.Candidates[0].MaxBid = &capPrice
		snap.PCTR = model(0.03)
		if got := priceFor(t, snap, parityReq); math.Abs(got-roundPrice(capPrice)) > 1e-6 {
			t.Fatalf("want max_bid %v, got %v", capPrice, got)
		}
	})
	t.Run("floor holds under a downward multiplier", func(t *testing.T) {
		snap := testSnapshot()
		snap.Candidates[0].Objective = "traffic"
		snap.PCTR = model(0.002)
		// floor = 90% базовой: 0.7x ушёл бы ниже, Python поднимает до floor.
		floor := base * 0.9
		req := strings.Replace(parityReq, `"bidfloor":1`, `"bidfloor":`+strconv.FormatFloat(floor, 'f', 4, 64), 1)
		if got := priceFor(t, snap, req); math.Abs(got-roundPrice(floor)) > 1e-3 {
			t.Fatalf("want floor %v, got %v", floor, got)
		}
	})
	t.Run("segment miss -> base price", func(t *testing.T) {
		snap := testSnapshot()
		snap.Candidates[0].Objective = "traffic"
		snap.PCTR = pctr.FromFeed(&pctr.FeedModel{Version: 1, GlobalCTR: 0.01,
			Segments: []pctr.Segment{{Type: "domain", Key: "someone-else.example", CTR: 0.5}}})
		if got := priceFor(t, snap, parityReq); got != base {
			t.Fatalf("matched=0 must keep base %v, got %v", base, got)
		}
	})
}

// Even pacing (порт rtb.py/business_day.even_pacing_budget): в 00:00 МСК
// кампании с pacing=even открыто только 10% дневного бюджета; asap-кампания
// с тем же бюджетом торгуется. К полудню МСК — 60%, и тот же бид проходит.
func TestParity_EvenPacingThrottlesDailyBudget(t *testing.T) {
	at := func(s string) time.Time { n, _ := time.Parse(time.RFC3339, s); return n }
	// price 120 CPM -> cost 0.12; бюджет 1.0: asap пропускает, even в полночь
	// даёт allowance 0.1 < 0.12 -> отказ гейта.
	run := func(t *testing.T, pacing string, now time.Time) int {
		t.Helper()
		snap := testSnapshot()
		snap.Candidates[0].Pacing = pacing
		snap.Candidates[0].CampBudget = 1.0
		h, _ := newMiniRedisBidHandler(t, snap)
		h.now = func() time.Time { return now }
		return doBid(t, h, parityReq).Code
	}
	midnightMSK := at("2026-09-06T21:00:00Z")
	noonMSK := at("2026-09-07T09:00:00Z")

	if code := run(t, "asap", midnightMSK); code != http.StatusOK {
		t.Fatalf("asap at business midnight must bid, got %d", code)
	}
	if code := run(t, "even", midnightMSK); code != http.StatusNoContent {
		t.Fatalf("even at business midnight: allowance 0.1 < cost 0.12 must be no-bid, got %d", code)
	}
	if code := run(t, "even", noonMSK); code != http.StatusOK {
		t.Fatalf("even at noon MSK: allowance 0.6 >= 0.12 must bid, got %d", code)
	}
	// UTC-полночь ничего не значит: 00:00 UTC = 03:00 МСК -> 22.5% -> бид проходит.
	if code := run(t, "even", at("2026-09-07T00:00:00Z")); code != http.StatusOK {
		t.Fatalf("even at UTC midnight (03:00 MSK) must bid, got %d", code)
	}
}

// Предфильтр (Часть C): бот-UA отсекается до таргетинга и аукциона, считается
// в bidder_prefilter_drops_total, а не в no-bid; без предфильтра тот же запрос
// торгуется.
func TestParity_PrefilterDropsBotBeforeAuction(t *testing.T) {
	botReq := strings.Replace(parityReq, "Mozilla/5.0 (Linux; Android 13) Chrome/120", "python-requests/2.31.0", 1)

	h := newTestHandler(testSnapshot())
	if w := doBid(t, h, botReq); w.Code != http.StatusOK {
		t.Fatalf("without prefilter the bot UA still bids (targeting has no UA rule): got %d", w.Code)
	}

	h = newTestHandler(testSnapshot())
	reg := metrics.New()
	h.SetMetrics(reg)
	h.SetPrefilter(prefilter.New(prefilter.Config{
		Enabled: true, RequireDeviceID: true, Countries: map[string]bool{"ru": true, "rus": true},
	}, nil))
	if w := doBid(t, h, botReq); w.Code != http.StatusNoContent {
		t.Fatalf("prefilter must drop bot UA with 204, got %d", w.Code)
	}
	if got := metricValue(t, reg, `bidder_prefilter_drops_total{reason="bot_ua"}`); got != "1" {
		t.Fatalf("bidder_prefilter_drops_total{bot_ua}: want 1, got %q", got)
	}
	if got := metricValue(t, reg, `bidder_no_bid_total{reason="no_candidates"}`); got != "0" {
		t.Fatalf("prefilter drop must not be counted as no-bid, got %q", got)
	}
	// Легитимный запрос через тот же фильтр торгуется как раньше.
	if w := doBid(t, h, parityReq); w.Code != http.StatusOK {
		t.Fatalf("legit request through prefilter must bid, got %d", w.Code)
	}
}

// Feed-парсер собирает те же структуры из JSON, что тесты выше собирают руками:
// регистр значений нормализуется, кампании разделены.
func TestParity_FeedParsesBlockLists(t *testing.T) {
	snap := &feed.Snapshot{
		OptBlocks: map[string]map[string]map[string]bool{},
	}
	for _, b := range []feed.OptimizerBlock{
		{CampaignID: "1", BlockType: "domain", BlockValue: "Pub.Example"},
		{CampaignID: "2", BlockType: "os", BlockValue: "iOS"},
	} {
		if snap.OptBlocks[b.CampaignID] == nil {
			snap.OptBlocks[b.CampaignID] = map[string]map[string]bool{}
		}
		if snap.OptBlocks[b.CampaignID][b.BlockType] == nil {
			snap.OptBlocks[b.CampaignID][b.BlockType] = map[string]bool{}
		}
		snap.OptBlocks[b.CampaignID][b.BlockType][strings.ToLower(b.BlockValue)] = true
	}
	if !snap.Blocked("1", "domain", "pub.example") || !snap.Blocked("1", "domain", "PUB.EXAMPLE") {
		t.Fatal("Blocked must be case-insensitive on value")
	}
	if snap.Blocked("1", "os", "ios") || !snap.Blocked("2", "os", "ios") {
		t.Fatal("blocks must be scoped per campaign")
	}
	if snap.Blocked("1", "domain", "") {
		t.Fatal("empty dimension value never matches (Python skips the check when req value is empty)")
	}
}
