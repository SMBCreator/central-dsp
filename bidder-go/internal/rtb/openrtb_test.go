package rtb

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// Контрактные фикстуры testdata/openrtb/*.json — общие с
// backend/tests/test_openrtb_compat.py (audit R-04). Каждая описывает imp,
// креатив, deal_ids line item и ожидание: причину несовместимости, собранные
// native assets, MIME/протоколы/Duration видео, выбранную сделку.

type openrtbFixture struct {
	Name     string          `json:"name"`
	Imp      Imp             `json:"imp"`
	Creative json.RawMessage `json:"creative"`
	DealIDs  []string        `json:"deal_ids"`
	Expect   struct {
		Incompat      *string          `json:"incompat"`
		NativeAssets  *json.RawMessage `json:"native_assets"`
		VideoMIME     string           `json:"video_mime"`
		VASTProtocols []int            `json:"vast_protocols"`
		VASTDuration  string           `json:"vast_duration"`
		Deal          *Deal            `json:"deal"`
	} `json:"expect"`
}

func loadOpenRTBFixtures(t *testing.T) map[string]openrtbFixture {
	t.Helper()
	files, err := filepath.Glob(filepath.Join("..", "..", "testdata", "openrtb", "*.json"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no fixtures: %v", err)
	}
	out := map[string]openrtbFixture{}
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var fx openrtbFixture
		if err := json.Unmarshal(b, &fx); err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		out[strings.TrimSuffix(filepath.Base(f), ".json")] = fx
	}
	return out
}

// fixtureCandidate — креатив фикстуры как Candidate (feed-поля, без БД).
func fixtureCandidate(t *testing.T, raw json.RawMessage) *feed.Candidate {
	t.Helper()
	var c feed.Candidate
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	return &c
}

// evalFixture — тот же порядок проверок, что в bidOnImp.
func evalFixture(t *testing.T, fx openrtbFixture) (incompat string, assets []map[string]any, deal *Deal) {
	t.Helper()
	c := fixtureCandidate(t, fx.Creative)
	nativeReq, err := parseNativeRequest(fx.Imp.Native)
	if err != nil {
		return IncompatNativeMalformed, nil, nil
	}
	deal = matchedDealObj(&fx.Imp, fx.DealIDs)
	if privateAuctionOnly(&fx.Imp) && deal == nil {
		return IncompatPrivateAuction, nil, nil
	}
	if fx.Imp.Video != nil {
		if r := videoIncompat(fx.Imp.Video, c); r != "" {
			return r, nil, deal
		}
	}
	if fx.Imp.Native != nil && nativeReq != nil {
		a, ok := buildNativeAssets(nativeReq, c)
		if !ok {
			return IncompatNativeAsset, nil, deal
		}
		assets = a
	}
	return "", assets, deal
}

func TestOpenRTB_Fixtures(t *testing.T) {
	fixtures := loadOpenRTBFixtures(t)
	names := make([]string, 0, len(fixtures))
	for n := range fixtures {
		names = append(names, n)
	}
	sort.Strings(names)
	if len(names) < 10 {
		t.Fatalf("expected >= 10 fixtures, got %d", len(names))
	}
	for _, name := range names {
		fx := fixtures[name]
		t.Run(name, func(t *testing.T) {
			c := fixtureCandidate(t, fx.Creative)
			incompat, assets, deal := evalFixture(t, fx)

			want := ""
			if fx.Expect.Incompat != nil {
				want = *fx.Expect.Incompat
			}
			if incompat != want {
				t.Fatalf("incompat = %q, want %q", incompat, want)
			}
			if fx.Expect.NativeAssets != nil {
				gotJSON, _ := json.Marshal(assets)
				var got, exp any
				_ = json.Unmarshal(gotJSON, &got)
				_ = json.Unmarshal(*fx.Expect.NativeAssets, &exp)
				if !reflect.DeepEqual(got, exp) {
					t.Fatalf("native assets:\n got %s\nwant %s", gotJSON, *fx.Expect.NativeAssets)
				}
			}
			if fx.Expect.VideoMIME != "" && creativeVideoMIME(c) != fx.Expect.VideoMIME {
				t.Fatalf("mime = %q, want %q", creativeVideoMIME(c), fx.Expect.VideoMIME)
			}
			if fx.Expect.VASTProtocols != nil && !reflect.DeepEqual(creativeVASTProtocols(c), fx.Expect.VASTProtocols) {
				t.Fatalf("protocols = %v, want %v", creativeVASTProtocols(c), fx.Expect.VASTProtocols)
			}
			if fx.Expect.VASTDuration != "" && vastDuration(creativeVideoDuration(c)) != fx.Expect.VASTDuration {
				t.Fatalf("duration = %q, want %q", vastDuration(creativeVideoDuration(c)), fx.Expect.VASTDuration)
			}
			if fx.Expect.Deal != nil {
				if deal == nil || !reflect.DeepEqual(*deal, *fx.Expect.Deal) {
					t.Fatalf("deal = %+v, want %+v", deal, fx.Expect.Deal)
				}
			} else if fx.Expect.Incompat != nil && *fx.Expect.Incompat == IncompatPrivateAuction && deal != nil {
				t.Fatalf("deal = %+v, want nil", deal)
			}
		})
	}
}

// --- Handler-level: seat, deal floor/at, native adm, метрика ---

type seatResp struct {
	SeatBid []struct {
		Seat string `json:"seat"`
		Bid  []struct {
			Price  float64 `json:"price"`
			DealID string  `json:"dealid"`
			AdM    string  `json:"adm"`
		} `json:"bid"`
	} `json:"seatbid"`
}

func bidJSON(t *testing.T, snap *feed.Snapshot, body string) (int, seatResp) {
	t.Helper()
	w := doBid(t, newTestHandler(snap), body)
	var r seatResp
	if w.Code == http.StatusOK {
		if err := json.Unmarshal(w.Body.Bytes(), &r); err != nil {
			t.Fatal(err)
		}
	}
	return w.Code, r
}

func TestOpenRTB_SeatFromPartner(t *testing.T) {
	snap := testSnapshot()
	snap.Partners["key1"].SeatID = "ssp-assigned-42"
	code, r := bidJSON(t, snap, fxReq("", 0, ""))
	if code != http.StatusOK || len(r.SeatBid) != 1 {
		t.Fatalf("code=%d seatbid=%d", code, len(r.SeatBid))
	}
	if r.SeatBid[0].Seat != "ssp-assigned-42" {
		t.Fatalf("seat = %q", r.SeatBid[0].Seat)
	}

	snap.Partners["key1"].SeatID = ""
	_, r = bidJSON(t, snap, fxReq("", 0, ""))
	if r.SeatBid[0].Seat != "central-dsp" {
		t.Fatalf("default seat = %q", r.SeatBid[0].Seat)
	}
}

func pmpReq(privateAuction int, deals []map[string]any) string {
	imp := map[string]any{
		"id": "1", "banner": map[string]int{"w": 300, "h": 250}, "bidfloor": 1.0,
		"pmp": map[string]any{"private_auction": privateAuction, "deals": deals},
	}
	req := map[string]any{
		"id": "pmp1", "at": 2, "imp": []any{imp},
		"site":   map[string]any{"domain": "pub.example"},
		"device": map[string]any{"ua": "Mozilla/5.0 (Linux; Android 13) Chrome/120", "os": "android", "devicetype": 1, "ifa": "dev-1", "geo": map[string]any{"country": "RUS"}},
	}
	b, _ := json.Marshal(req)
	return string(b)
}

func TestOpenRTB_PrivateAuctionWithoutDeal_NoBid(t *testing.T) {
	snap := testSnapshot()
	snap.Partners["key1"].BidFloor = 0
	code, _ := bidJSON(t, snap, pmpReq(1, []map[string]any{{"id": "D-777"}}))
	if code != http.StatusNoContent {
		t.Fatalf("code = %d, want 204 (private auction, no deal)", code)
	}
	// Открытый аукцион с чужими сделками — торгуемся без dealid.
	code, r := bidJSON(t, snap, pmpReq(0, []map[string]any{{"id": "D-777"}}))
	if code != http.StatusOK || r.SeatBid[0].Bid[0].DealID != "" {
		t.Fatalf("open auction: code=%d dealid=%q", code, r.SeatBid[0].Bid[0].DealID)
	}
}

func TestOpenRTB_DealFloorOverridesImpFloor(t *testing.T) {
	snap := testSnapshot()
	snap.Partners["key1"].BidFloor = 0
	snap.Candidates[0].T = &feed.Targeting{DealIDs: []string{"D-1"}}
	// Кандидат 120 ₽; imp floor 1 ₽, но сделка требует 150 ₽ -> no-bid.
	code, _ := bidJSON(t, snap, pmpReq(1, []map[string]any{{"id": "D-1", "bidfloor": 150.0}}))
	if code != http.StatusNoContent {
		t.Fatalf("deal floor 150 > bid 120: code = %d, want 204", code)
	}
	// Сделка с floor 100 ₽ -> бид 120 с dealid.
	code, r := bidJSON(t, snap, pmpReq(1, []map[string]any{{"id": "D-1", "bidfloor": 100.0}}))
	if code != http.StatusOK {
		t.Fatalf("code = %d", code)
	}
	b := r.SeatBid[0].Bid[0]
	if b.DealID != "D-1" || b.Price != 120 {
		t.Fatalf("bid = %+v, want dealid D-1 price 120", b)
	}
}

func TestOpenRTB_NativeAdmUsesRequestedAssets(t *testing.T) {
	snap := testSnapshot()
	snap.Partners["key1"].BidFloor = 0
	c := &snap.Candidates[0]
	c.Type = "native"
	c.NativeTitle = "Заголовок"
	c.NativeDesc = "Описание"
	c.Width, c.Height = nil, nil
	native := map[string]any{"request": `{"assets":[{"id":7,"required":1,"title":{"len":90}},{"id":8,"required":0,"img":{"type":3}}]}`}
	req := map[string]any{
		"id": "n1", "imp": []any{map[string]any{"id": "1", "native": native}},
		"site":   map[string]any{"domain": "pub.example"},
		"device": map[string]any{"ua": "Mozilla/5.0 (Linux; Android 13) Chrome/120", "os": "android", "devicetype": 1, "ifa": "dev-1", "geo": map[string]any{"country": "RUS"}},
	}
	body, _ := json.Marshal(req)
	code, r := bidJSON(t, snap, string(body))
	if code != http.StatusOK {
		t.Fatalf("code = %d", code)
	}
	var adm struct {
		Native struct {
			Assets []map[string]any `json:"assets"`
		} `json:"native"`
	}
	if err := json.Unmarshal([]byte(r.SeatBid[0].Bid[0].AdM), &adm); err != nil {
		t.Fatalf("adm json: %v", err)
	}
	if len(adm.Native.Assets) != 2 || adm.Native.Assets[0]["id"] != float64(7) || adm.Native.Assets[1]["id"] != float64(8) {
		t.Fatalf("assets = %v, want ids 7,8", adm.Native.Assets)
	}
	if _, has := adm.Native.Assets[0]["title"]; !has {
		t.Fatalf("asset 7 must be title: %v", adm.Native.Assets[0])
	}
}

func TestOpenRTB_NativeRequestObjectAccepted(t *testing.T) {
	// request объектом (нарушение спеки) не должен ронять разбор BidRequest.
	var req BidRequest
	if err := json.Unmarshal([]byte(`{"id":"x","imp":[{"id":"1","native":{"request":{"assets":[{"id":1,"title":{"len":5}}]}}}]}`), &req); err != nil {
		t.Fatal(err)
	}
	nr, err := parseNativeRequest(req.Imp[0].Native)
	if err != nil || nr == nil || len(nr.Assets) != 1 || nr.Assets[0].Title.Len != 5 {
		t.Fatalf("parse = %+v, %v", nr, err)
	}
}

func TestOpenRTB_VASTDurationHHMMSS(t *testing.T) {
	for sec, want := range map[int]string{0: "00:00:00", 15: "00:00:15", 90: "00:01:30", 3725: "01:02:05"} {
		if got := vastDuration(sec); got != want {
			t.Errorf("vastDuration(%d) = %q, want %q", sec, got, want)
		}
	}
}
