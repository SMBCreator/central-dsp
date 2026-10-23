package rtb

// Ретаргетинг-сегменты в Go-биддере: паритет с backend/audiences.py.
// Redis — miniredis: ключи audz:{segment} (ZSET device -> expires_at) наполняет
// пиксель, биддер только читает.

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/redisx"
)

func newMiniRedisHandler(t testing.TB) (*Handler, *miniredis.Miniredis) {
	t.Helper()
	mr := miniredis.RunT(t)
	rc, err := redisx.New("redis://" + mr.Addr() + "/0")
	if err != nil {
		t.Fatalf("redisx.New: %v", err)
	}
	t.Cleanup(func() { _ = rc.Close() })
	return &Handler{redis: rc}, mr
}

// audFuture — expires_at участника далеко в будущем (сегмент ещё жив).
var audFuture = float64(time.Now().Add(24 * time.Hour).Unix())

// Просроченный участник (score в прошлом) — уже не в сегменте, даже если
// pruner его ещё не удалил (R-06 per-member expiry).
func TestAudienceOK_ExpiredMemberIsNotAMember(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	mr.ZAdd("audz:buyers", float64(time.Now().Add(-time.Minute).Unix()), "dev-1")
	mr.ZAdd("audz:buyers", audFuture, "dev-2")
	ctx := context.Background()
	if h.audienceOK(ctx, []string{"buyers"}, nil, "dev-1") {
		t.Fatal("expired include member must not pass")
	}
	if !h.audienceOK(ctx, []string{"buyers"}, nil, "dev-2") {
		t.Fatal("live member must pass")
	}
	if !h.audienceOK(ctx, nil, []string{"buyers"}, "dev-1") {
		t.Fatal("expired exclude member must not block")
	}
	if h.audienceOK(ctx, nil, []string{"buyers"}, "dev-2") {
		t.Fatal("live exclude member must block")
	}
}

func TestAudienceOK_NoSegmentsAlwaysPasses(t *testing.T) {
	h := &Handler{} // redis == nil: без сегментов Redis не нужен
	if !h.audienceOK(context.Background(), nil, nil, "dev-1") {
		t.Fatal("no segments must pass")
	}
	if !h.audienceOK(context.Background(), nil, nil, "") {
		t.Fatal("no segments, no device must pass")
	}
}

func TestAudienceOK_IncludeExclude(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	ctx := context.Background()
	mr.ZAdd("audz:buyers", audFuture, "dev-1")
	mr.ZAdd("audz:buyers", audFuture, "dev-2")
	mr.ZAdd("audz:churned", audFuture, "dev-2")

	cases := []struct {
		name    string
		include []string
		exclude []string
		device  string
		want    bool
	}{
		{"in include", []string{"buyers"}, nil, "dev-1", true},
		{"not in include", []string{"buyers"}, nil, "dev-9", false},
		{"any of several includes", []string{"vip", "buyers"}, nil, "dev-1", true},
		{"in exclude", nil, []string{"churned"}, "dev-2", false},
		{"not in exclude", nil, []string{"churned"}, "dev-1", true},
		{"include ok but excluded", []string{"buyers"}, []string{"churned"}, "dev-2", false},
		{"include ok not excluded", []string{"buyers"}, []string{"churned"}, "dev-1", true},
		{"unknown device: include unsatisfiable", []string{"buyers"}, nil, "", false},
		{"unknown device: exclude irrelevant", nil, []string{"churned"}, "", true},
		{"missing segment key = not member", []string{"nope"}, nil, "dev-1", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := h.audienceOK(ctx, tc.include, tc.exclude, tc.device); got != tc.want {
				t.Fatalf("audienceOK(%v,%v,%q)=%v want %v", tc.include, tc.exclude, tc.device, got, tc.want)
			}
		})
	}
}

func TestAudienceOK_FailOpenOnRedisDown(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	mr.Close() // соединение падает -> fail-open, как в Python
	if !h.audienceOK(context.Background(), []string{"buyers"}, nil, "dev-1") {
		t.Fatal("redis error must fail-open (trade as if no segments)")
	}
}

// newMiniRedisBidHandler — полный handler (store+redis на miniredis) для доводки
// через doBid. test=1 в запросах: бюджет не резервируется, Lua-скрипты не нужны.
func newMiniRedisBidHandler(t *testing.T, snap *feed.Snapshot) (*Handler, *miniredis.Miniredis) {
	t.Helper()
	mr := miniredis.RunT(t)
	rc, err := redisx.New("redis://" + mr.Addr() + "/0")
	if err != nil {
		t.Fatalf("redisx.New: %v", err)
	}
	t.Cleanup(func() { _ = rc.Close() })
	store := feed.NewStore(feed.Config{BackendURL: "http://localhost:0"})
	store.SetSnapshot(snap)
	return NewHandler(store, rc), mr
}

const audienceBidReq = `{"id":"r1","test":1,"imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":1}],` +
	`"device":{"ifa":"dev-1","ua":"Mozilla/5.0"},"site":{"domain":"pub.example"}}`

func TestBid_AudienceFiltersCandidates(t *testing.T) {
	// Два кандидата: дорогой с include-сегментом, в котором устройства нет, и
	// дешёвый без сегментов. Побеждает дешёвый; после добавления устройства в
	// сегмент — дорогой.
	snap := testSnapshot()
	seg := snap.Candidates[0]
	seg.LiID, seg.CreativeID, seg.BidPrice = "li-seg", "cr-seg", 90
	seg.T = &feed.Targeting{AudienceInclude: []string{"buyers"}}
	plain := snap.Candidates[0]
	plain.LiID, plain.CreativeID, plain.BidPrice = "li-plain", "cr-plain", 50
	snap.Candidates = []feed.Candidate{seg, plain}

	h, mr := newMiniRedisBidHandler(t, snap)
	mr.ZAdd("audz:buyers", audFuture, "other-device")

	w := doBid(t, h, audienceBidReq)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp BidResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if got := resp.SeatBid[0].Bid[0].CrID; got != "cr-plain" {
		t.Fatalf("segment-gated candidate must be filtered; got crid=%s", got)
	}

	mr.ZAdd("audz:buyers", audFuture, "dev-1")
	w = doBid(t, h, audienceBidReq)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	resp = BidResponse{}
	_ = json.Unmarshal(w.Body.Bytes(), &resp)
	if got := resp.SeatBid[0].Bid[0].CrID; got != "cr-seg" {
		t.Fatalf("device in segment: expected cr-seg, got %s", got)
	}
}

func TestBid_ExcludeSegmentDropsOnlyCandidate(t *testing.T) {
	snap := testSnapshot()
	snap.Candidates[0].T = &feed.Targeting{AudienceExclude: []string{"churned"}}
	h, mr := newMiniRedisBidHandler(t, snap)
	mr.ZAdd("audz:churned", audFuture, "dev-1")
	if w := doBid(t, h, audienceBidReq); w.Code != http.StatusNoContent {
		t.Fatalf("excluded device: want 204 no-bid, got %d", w.Code)
	}
	mr.ZRem("audz:churned", "dev-1")
	if w := doBid(t, h, audienceBidReq); w.Code != http.StatusOK {
		t.Fatalf("device not excluded: want 200, got %d", w.Code)
	}
}

func TestBid_WritesClidmapBridge(t *testing.T) {
	// Контракт с audiences.resolve_clid: ключ clidmap:{bid_id}, TTL 7 суток.
	h, mr := newMiniRedisBidHandler(t, testSnapshot())
	w := doBid(t, h, audienceBidReq)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	var resp BidResponse
	_ = json.Unmarshal(w.Body.Bytes(), &resp)
	bidID := resp.SeatBid[0].Bid[0].ID

	got, err := mr.Get("clidmap:" + bidID)
	if err != nil || got != "dev-1" {
		t.Fatalf("clidmap:%s = %q, %v; want dev-1", bidID, got, err)
	}
	if ttl := mr.TTL("clidmap:" + bidID); ttl < clidMapTTL-clidMapTTL/10 {
		t.Fatalf("clidmap TTL too short: %v", ttl)
	}
	if mr.Exists("clid:" + bidID) {
		t.Fatal("legacy clid:{bid} key must not be written anymore")
	}
}
