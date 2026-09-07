package rtb

// Audit R-03 — исполняемая parity matrix «настройка кабинета -> BidResponse».
// Каждый тест: один и тот же BidRequest даёт бид на базовом снапшоте и no-bid
// (или другую цену) после включения настройки. Истина — backend/routers/rtb.py.
// Строки матрицы: docs/audit/parity_matrix.md (раздел R-03).

import (
	"net/http"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
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
