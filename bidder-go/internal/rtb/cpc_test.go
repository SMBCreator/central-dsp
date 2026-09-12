package rtb

// Audit R-03 (B8.4): CPC line items. Go торгует эквивалентной CPM
// (bid_cpc × expected CTR × 1000), пишет bid_type/bid_cpc в bidmeta;
// деньги списывает Python click(), win() CPC-бид не биллит.
// Числа зеркалят backend/tests/test_cpc.py.

import (
	"encoding/json"
	"math"
	"net/http"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/pctr"
)

const cpcBidReq = `{"id":"r-cpc","imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":1}],` +
	`"device":{"ifa":"dev-cpc","ua":"Mozilla/5.0"},"site":{"domain":"pub.example"}}`

func cpcSnapshot(bidCPC float64, maxBid *float64, model *pctr.FeedModel) *feed.Snapshot {
	snap := testSnapshot()
	c := &snap.Candidates[0]
	c.BidType = "cpc"
	c.BidPrice = bidCPC
	c.MaxBid = maxBid
	snap.PCTR = pctr.FromFeed(model)
	return snap
}

func readBidMeta(t *testing.T, keys []string, get func(string) (string, error)) map[string]any {
	t.Helper()
	for _, k := range keys {
		if strings.HasPrefix(k, "bidmeta:") {
			raw, err := get(k)
			if err != nil {
				t.Fatalf("get %s: %v", k, err)
			}
			var m map[string]any
			if err := json.Unmarshal([]byte(raw), &m); err != nil {
				t.Fatalf("bidmeta json: %v", err)
			}
			return m
		}
	}
	t.Fatalf("bidmeta not written; keys=%v", keys)
	return nil
}

func TestCPCToCPM_MirrorsPython(t *testing.T) {
	cases := []struct{ cpc, ctr, want float64 }{
		{50, pctr.CPCFallbackCTR, 100}, // 50 ₽/click × 0.2% × 1000 = 100 CPM
		{50, 0.01, 500},                // 1% CTR
		{12.5, 0.004, 50},              // 12.5 × 0.004 × 1000
		{0, 0.01, 0},
	}
	for _, c := range cases {
		if got := pctr.CPCToCPM(c.cpc, c.ctr); math.Abs(got-c.want) > 1e-9 {
			t.Fatalf("CPCToCPM(%v,%v)=%v want %v", c.cpc, c.ctr, got, c.want)
		}
	}
}

func TestExpectedCTR_FallbackAndModel(t *testing.T) {
	var nilModel *pctr.Model
	if got := nilModel.ExpectedCTR("pub.example", "21", "mobile", 12); got != pctr.CPCFallbackCTR {
		t.Fatalf("nil model: want fallback %v, got %v", pctr.CPCFallbackCTR, got)
	}
	m := pctr.FromFeed(&pctr.FeedModel{Version: 1, GlobalCTR: 0.005})
	if got := m.ExpectedCTR("pub.example", "21", "mobile", 12); math.Abs(got-0.005) > 1e-12 {
		t.Fatalf("no matched segments: want global ctr 0.005, got %v", got)
	}
	m = pctr.FromFeed(&pctr.FeedModel{Version: 1, GlobalCTR: 0.005,
		Segments: []pctr.Segment{{Type: "domain", Key: "pub.example", CTR: 0.02}}})
	if got := m.ExpectedCTR("PUB.example", "21", "mobile", 12); got <= 0.005 {
		t.Fatalf("matched segment must move ctr above prior, got %v", got)
	}
}

func TestBid_CPCPricesAsExpectedCPM(t *testing.T) {
	// Без модели: 50 ₽/click × 0.002 × 1000 = 100 CPM.
	h, mr := newMiniRedisBidHandler(t, cpcSnapshot(50, nil, nil))
	w := doBid(t, h, cpcBidReq)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200 bid, got %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		SeatBid []struct {
			Bid []struct {
				Price float64 `json:"price"`
			} `json:"bid"`
		} `json:"seatbid"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("response json: %v", err)
	}
	if len(resp.SeatBid) != 1 || len(resp.SeatBid[0].Bid) != 1 {
		t.Fatalf("want exactly one bid, got %s", w.Body.String())
	}
	if got := resp.SeatBid[0].Bid[0].Price; math.Abs(got-100) > 1e-6 {
		t.Fatalf("CPC bid must be priced as CPM equivalent 100, got %v", got)
	}

	meta := readBidMeta(t, mr.Keys(), mr.Get)
	if meta["bid_type"] != "cpc" {
		t.Fatalf("bidmeta.bid_type: want cpc, got %v", meta["bid_type"])
	}
	if cpc, _ := meta["bid_cpc"].(float64); math.Abs(cpc-50) > 1e-9 {
		t.Fatalf("bidmeta.bid_cpc: want 50, got %v", meta["bid_cpc"])
	}
	if p, _ := meta["price"].(float64); math.Abs(p-100) > 1e-6 {
		t.Fatalf("bidmeta.price must be CPM equivalent 100, got %v", meta["price"])
	}
	// Резерв — ожидаемая стоимость показа (CPM/1000), не цена клика.
	if r, _ := meta["reserve"].(float64); math.Abs(r-0.1) > 1e-9 {
		t.Fatalf("bidmeta.reserve: want 0.1 (100/1000), got %v", meta["reserve"])
	}
}

func TestBid_CPCMaxBidCapsClickPriceNotCPM(t *testing.T) {
	// max_bid=20 — потолок клика: 20 × 0.002 × 1000 = 40 CPM (не min(100, 20)).
	maxBid := 20.0
	h, mr := newMiniRedisBidHandler(t, cpcSnapshot(50, &maxBid, nil))
	w := doBid(t, h, cpcBidReq)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200 bid, got %d: %s", w.Code, w.Body.String())
	}
	meta := readBidMeta(t, mr.Keys(), mr.Get)
	if cpc, _ := meta["bid_cpc"].(float64); math.Abs(cpc-20) > 1e-9 {
		t.Fatalf("bid_cpc capped by max_bid: want 20, got %v", meta["bid_cpc"])
	}
	if p, _ := meta["price"].(float64); math.Abs(p-40) > 1e-6 {
		t.Fatalf("price: want 40 CPM, got %v", meta["price"])
	}
}

func TestBid_CPCBelowFloorIsNoBid(t *testing.T) {
	// 2 ₽/click × 0.002 × 1000 = 4 CPM < partner floor 10 -> no-bid.
	h, _ := newMiniRedisBidHandler(t, cpcSnapshot(2, nil, nil))
	w := doBid(t, h, cpcBidReq)
	if w.Code != http.StatusNoContent {
		t.Fatalf("CPC equivalent below floor: want 204, got %d: %s", w.Code, w.Body.String())
	}
}

func TestBid_CPMBidMetaHasNullCPC(t *testing.T) {
	h, mr := newMiniRedisBidHandler(t, testSnapshot())
	w := doBid(t, h, cpcBidReq)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200 bid, got %d: %s", w.Code, w.Body.String())
	}
	meta := readBidMeta(t, mr.Keys(), mr.Get)
	if meta["bid_type"] != "cpm" {
		t.Fatalf("bidmeta.bid_type: want cpm, got %v", meta["bid_type"])
	}
	if v, ok := meta["bid_cpc"]; !ok || v != nil {
		t.Fatalf("bidmeta.bid_cpc must be null for CPM (Python writes None), got %v", v)
	}
}
