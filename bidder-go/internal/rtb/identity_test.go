package rtb

// ID sync в биддере (план predeploy A.6/A.8): порядок идентификации, lookup
// idsync:* в Redis, деградация при недоступном Redis, флаг off = прежнее поведение,
// интеграция «user.id -> dsp_uid -> аудитория».

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
)

const testSalt = "salt-1"

func syncPartner() *feed.Partner {
	return &feed.Partner{ID: "5", IDSyncSalt: testSalt, Identity: &feed.PartnerIdentity{SyncEnabled: true, IDSource: "user_id"}}
}

// Общий вектор с backend/tests/test_id_sync.py::test_hash_vector_matches_go.
func TestIDSyncKey_MatchesBackendVector(t *testing.T) {
	got := IDSyncKey("p1", "salt-1", "ssp-user-42")
	want := "idsync:p1:17d9964dcad2521174bf8e4c162febb0133cd95aa32795ec8d9ea81090f6c7f9"
	if got != want {
		t.Fatalf("hash drift with backend: got %s want %s", got, want)
	}
}

func TestResolveIdentity_Order(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	p := syncPartner()
	dsp := "0123456789abcdef0123456789abcdef"
	mr.Set(IDSyncKey(p.ID, testSalt, "ssp-u"), dsp)

	full := func() *BidRequest {
		return &BidRequest{
			User:   &User{ID: "ssp-u", BuyerUID: "buyer-1", EIDs: []EID{{Source: "id5-sync.com", UIDs: []UID{{ID: "eid-1"}}}}},
			Device: &Device{IFA: "ifa-1"},
		}
	}
	ctx := context.Background()
	cases := []struct {
		name     string
		mutate   func(r *BidRequest)
		key, src string
	}{
		{"buyeruid first", func(r *BidRequest) {}, "buyer-1", IdentityBuyerUID},
		{"eids next", func(r *BidRequest) { r.User.BuyerUID = "" }, "eid-1", IdentityEIDs},
		{"ifa next", func(r *BidRequest) { r.User.BuyerUID = ""; r.User.EIDs = nil }, "ifa-1", IdentityIFA},
		{"zero ifa skipped -> idsync", func(r *BidRequest) {
			r.User.BuyerUID = ""
			r.User.EIDs = nil
			r.Device.IFA = "00000000-0000-0000-0000-000000000000"
		}, dsp, IdentityIDSync},
		{"unsynced user.id -> namespace", func(r *BidRequest) {
			r.User.BuyerUID, r.User.EIDs, r.Device = "", nil, nil
			r.User.ID = "other"
		}, "p:5:other", IdentityUserID},
		{"nothing -> anonymous", func(r *BidRequest) { r.User, r.Device = nil, nil }, "", IdentityAnonymous},
		{"control chars rejected", func(r *BidRequest) {
			r.User.BuyerUID = "a\r\nb"
			r.User.EIDs, r.Device = nil, nil
			r.User.ID = ""
		}, "", IdentityAnonymous},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := full()
			tc.mutate(r)
			key, src := h.resolveIdentity(ctx, r, p)
			if key != tc.key || src != tc.src {
				t.Fatalf("got (%q,%s) want (%q,%s)", key, src, tc.key, tc.src)
			}
		})
	}
}

func TestResolveIdentity_EIDsSourceFilter(t *testing.T) {
	h := &Handler{}
	p := &feed.Partner{ID: "5", Identity: &feed.PartnerIdentity{IDSource: "eids:ssp.example"}}
	r := &BidRequest{User: &User{EIDs: []EID{
		{Source: "other.example", UIDs: []UID{{ID: "x"}}},
		{Source: "ssp.example", UIDs: []UID{{ID: "y"}}},
	}}}
	if key, src := h.resolveIdentity(context.Background(), r, p); key != "y" || src != IdentityEIDs {
		t.Fatalf("configured eids source must win: got %q %s", key, src)
	}
}

func TestResolveIdentity_SyncDisabledForPartnerSkipsRedis(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	p := syncPartner()
	p.Identity.SyncEnabled = false
	mr.Set(IDSyncKey(p.ID, testSalt, "ssp-u"), "0123456789abcdef0123456789abcdef")
	key, src := h.resolveIdentity(context.Background(), &BidRequest{User: &User{ID: "ssp-u"}}, p)
	if key != "p:5:ssp-u" || src != IdentityUserID {
		t.Fatalf("partner without sync must not lookup: %q %s", key, src)
	}
}

func TestResolveIdentity_RedisDownFallsBackNotNoBid(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	h.metrics = metrics.New()
	mr.Close()
	key, src := h.resolveIdentity(context.Background(), &BidRequest{User: &User{ID: "ssp-u"}}, syncPartner())
	if key != "p:5:ssp-u" || src != IdentityUserID {
		t.Fatalf("redis down must fall back to namespaced user.id: %q %s", key, src)
	}
}

func TestIdentityOf_FlagOffIsLegacy(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	p := syncPartner()
	mr.Set(IDSyncKey(p.ID, testSalt, "ssp-u"), "0123456789abcdef0123456789abcdef")
	reqs := []*BidRequest{
		{User: &User{ID: "ssp-u", BuyerUID: "b"}, Device: &Device{IFA: "ifa"}},
		{User: &User{ID: "ssp-u", BuyerUID: "b"}},
		{User: &User{BuyerUID: "b"}},
		{User: &User{ID: "ssp-u"}},
		{},
	}
	for i, r := range reqs {
		if got, want := h.identityOf(context.Background(), r, p), deviceIDOf(r, p.ID); got != want {
			t.Fatalf("case %d: flag off must equal deviceIDOf: %q vs %q", i, got, want)
		}
	}
}

// Интеграция A.8: SSP прислал только user.id, sync сделан ранее (idsync:* в
// Redis) -> identity = dsp_uid -> кандидат с include-сегментом по dsp_uid выигрывает,
// а clidmap-мост пишет dsp_uid (пиксель пополнит ту же аудиторию).
func TestBid_IDSyncResolvesAudienceMember(t *testing.T) {
	snap := testSnapshot()
	snap.Partners["key1"].IDSyncSalt = testSalt
	snap.Partners["key1"].Identity = &feed.PartnerIdentity{SyncEnabled: true, IDSource: "user_id"}
	seg := snap.Candidates[0]
	seg.LiID, seg.CreativeID, seg.BidPrice = "li-seg", "cr-seg", 90
	seg.T = &feed.Targeting{AudienceInclude: []string{"buyers"}}
	plain := snap.Candidates[0]
	plain.LiID, plain.CreativeID, plain.BidPrice = "li-plain", "cr-plain", 50
	snap.Candidates = []feed.Candidate{seg, plain}

	h, mr := newMiniRedisBidHandler(t, snap)
	reg := metrics.New()
	h.SetMetrics(reg)
	dsp := "0123456789abcdef0123456789abcdef"
	mr.ZAdd("audz:buyers", audFuture, dsp)
	mr.Set(IDSyncKey("5", testSalt, "ssp-u"), dsp)
	body := `{"id":"r1","test":1,"imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":1}],` +
		`"user":{"id":"ssp-u"},"device":{"ua":"Mozilla/5.0","ip":"203.0.113.5"},"site":{"domain":"pub.example"}}`

	crid := func() string {
		w := doBid(t, h, body)
		if w.Code != http.StatusOK {
			t.Fatalf("want 200, got %d: %s", w.Code, w.Body.String())
		}
		var resp BidResponse
		_ = json.Unmarshal(w.Body.Bytes(), &resp)
		return resp.SeatBid[0].Bid[0].CrID + "|" + resp.SeatBid[0].Bid[0].ID
	}

	// Флаг off: identity = p:5:ssp-u, в сегменте нет -> дешёвый кандидат.
	if got := crid(); !strings.HasPrefix(got, "cr-plain|") {
		t.Fatalf("flag off: want cr-plain, got %s", got)
	}
	h.SetIDSync(true)
	got := crid()
	if !strings.HasPrefix(got, "cr-seg|") {
		t.Fatalf("flag on: synced user must match audience, got %s", got)
	}
	bidID := strings.SplitN(got, "|", 2)[1]
	if v, _ := mr.Get("clidmap:" + bidID); v != dsp {
		t.Fatalf("clidmap must carry dsp_uid, got %q", v)
	}

	rec := doMetrics(reg)
	for _, want := range []string{
		`bidder_identity_source_total{partner="5",source="idsync"} 1`,
		`bidder_audience_match_total{partner="5",result="match"} 1`,
		`bidder_idsync_lookup_latency_seconds_count 1`,
	} {
		if !strings.Contains(rec, want) {
			t.Fatalf("metrics missing %q:\n%s", want, rec)
		}
	}
}

func doMetrics(reg *metrics.Registry) string {
	w := httptest.NewRecorder()
	reg.Handler().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	return w.Body.String()
}

// Замер hot path (правило 4 плана): off = прежний deviceIDOf; on без sync у
// партнёра — без Redis; on с промахом — один GET (miniredis, верхняя оценка).
func BenchmarkIdentityOf(b *testing.B) {
	h, _ := newMiniRedisHandler(b)
	req := &BidRequest{User: &User{ID: "ssp-u"}, Device: &Device{UA: "Mozilla/5.0"}}
	plain := &feed.Partner{ID: "5"}
	synced := syncPartner()
	ctx := context.Background()
	b.Run("flag_off", func(b *testing.B) {
		h.SetIDSync(false)
		for i := 0; i < b.N; i++ {
			_ = h.identityOf(ctx, req, synced)
		}
	})
	b.Run("flag_on_partner_without_sync", func(b *testing.B) {
		h.SetIDSync(true)
		for i := 0; i < b.N; i++ {
			_ = h.identityOf(ctx, req, plain)
		}
	})
	b.Run("flag_on_redis_lookup", func(b *testing.B) {
		h.SetIDSync(true)
		for i := 0; i < b.N; i++ {
			_ = h.identityOf(ctx, req, synced)
		}
	})
}
