package rtb

import (
	"encoding/json"
	"math"
	"net/http"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/fx"
)

// Валютный паритет с backend/tests/test_fx_bidding.py (audit R-03). Числа
// общие: кандидат 120 ₽ CPM, партнёр в USD, курс ЦБ 90, наценка по умолчанию
// 3% -> effective 92.7. Ставка = 120/92.7 = 1.2945 USD; floor 1 USD = 92.7 ₽.

const fxUSDRate = 90.0
const fxEffective = 92.7 // 90 × 1.03

func fxSnapshot(partnerCur string, rates map[string]float64) *feed.Snapshot {
	snap := testSnapshot()
	snap.Partners["key1"].Currency = partnerCur
	snap.Partners["key1"].BidFloor = 0
	snap.FXRates = rates
	return snap
}

func fxReq(cur string, bidfloor float64, bidfloorcur string) string {
	imp := map[string]any{"id": "1", "banner": map[string]int{"w": 300, "h": 250}, "bidfloor": bidfloor}
	if bidfloorcur != "" {
		imp["bidfloorcur"] = bidfloorcur
	}
	req := map[string]any{
		"id": "fx1", "at": 1, "imp": []any{imp},
		"site":   map[string]any{"domain": "pub.example", "publisher": map[string]any{"id": "pub-9"}},
		"device": map[string]any{"ua": "Mozilla/5.0 (Linux; Android 13) Chrome/120", "os": "android", "devicetype": 1, "ifa": "dev-1", "geo": map[string]any{"country": "RUS"}},
	}
	if cur != "" {
		req["cur"] = []string{cur}
	}
	b, _ := json.Marshal(req)
	return string(b)
}

type fxResp struct {
	Cur     string `json:"cur"`
	SeatBid []struct {
		Bid []struct {
			Price float64 `json:"price"`
		} `json:"bid"`
	} `json:"seatbid"`
}

func fxBid(t *testing.T, snap *feed.Snapshot, req string) (int, fxResp) {
	t.Helper()
	w := doBid(t, newTestHandler(snap), req)
	var r fxResp
	if w.Code == http.StatusOK {
		if err := json.Unmarshal(w.Body.Bytes(), &r); err != nil {
			t.Fatalf("response json: %v", err)
		}
	}
	return w.Code, r
}

func TestFX_ChooseCurrency_MirrorsPython(t *testing.T) {
	cases := []struct {
		req     []string
		partner string
		want    string
	}{
		{nil, "", "RUB"},
		{nil, "USD", "USD"},
		{[]string{"USD"}, "USD", "USD"},
		{[]string{"usd", "rub"}, "USD", "USD"},
		{[]string{"RUB"}, "USD", "RUB"}, // SSP берёт только рубли — биддим в RUB
		{[]string{"USD", "RUB"}, "EUR", "RUB"},
		{[]string{"USD"}, "RUB", ""}, // рублёвый партнёр, SSP хочет USD — no-bid
		{[]string{"USD"}, "EUR", ""},
	}
	for _, c := range cases {
		if got := fx.ChooseCurrency(c.req, c.partner); got != c.want {
			t.Errorf("ChooseCurrency(%v,%q)=%q want %q", c.req, c.partner, got, c.want)
		}
	}
}

func TestFX_EffectiveRate_MirrorsPython(t *testing.T) {
	rates := map[string]float64{"USD": fxUSDRate}
	if r, ok := fx.EffectiveRate("RUB", nil, nil); !ok || r != 1 {
		t.Fatalf("RUB rate must be 1, got %v %v", r, ok)
	}
	if r, ok := fx.EffectiveRate("USD", rates, nil); !ok || math.Abs(r-fxEffective) > 1e-9 {
		t.Fatalf("default markup 3%%: want %v, got %v", fxEffective, r)
	}
	m := 10.0
	if r, ok := fx.EffectiveRate("usd", rates, &m); !ok || math.Abs(r-99) > 1e-9 {
		t.Fatalf("markup 10%%: want 99, got %v", r)
	}
	if _, ok := fx.EffectiveRate("EUR", rates, nil); ok {
		t.Fatal("missing rate must not be ok")
	}
	if _, ok := fx.EffectiveRate("USD", map[string]float64{"USD": 0}, nil); ok {
		t.Fatal("zero rate must not be ok")
	}
}

func TestFX_FloorCurrency_MirrorsPython(t *testing.T) {
	if got := fx.FloorCurrency("eur", []string{"USD"}, "RUB"); got != "EUR" {
		t.Fatalf("bidfloorcur wins: %q", got)
	}
	if got := fx.FloorCurrency("", []string{"USD", "RUB"}, "EUR"); got != "USD" {
		t.Fatalf("req.cur[0]: %q", got)
	}
	if got := fx.FloorCurrency("", nil, "usd"); got != "USD" {
		t.Fatalf("partner currency: %q", got)
	}
	if got := fx.FloorCurrency("", nil, ""); got != "RUB" {
		t.Fatalf("default RUB: %q", got)
	}
}

func TestFX_USDPartner_PricesInUSD(t *testing.T) {
	snap := fxSnapshot("USD", map[string]float64{"USD": fxUSDRate})
	code, r := fxBid(t, snap, fxReq("USD", 1, ""))
	if code != http.StatusOK {
		t.Fatalf("want 200, got %d", code)
	}
	if r.Cur != "USD" {
		t.Fatalf("cur: want USD, got %q", r.Cur)
	}
	want := roundPrice(120.0 / fxEffective) // 1.2945
	if got := r.SeatBid[0].Bid[0].Price; math.Abs(got-want) > 1e-6 {
		t.Fatalf("price: want %v USD, got %v", want, got)
	}
}

func TestFX_ImpFloorConvertedToRUB(t *testing.T) {
	snap := fxSnapshot("USD", map[string]float64{"USD": fxUSDRate})
	// 1.2 USD × 92.7 = 111.24 ₽ < 120 ₽ — торгуем.
	if code, _ := fxBid(t, snap, fxReq("USD", 1.2, "")); code != http.StatusOK {
		t.Fatalf("floor 1.2 USD (111.24 RUB) must pass, got %d", code)
	}
	// 1.3 USD × 92.7 = 120.51 ₽ > 120 ₽ — no-bid.
	if code, _ := fxBid(t, snap, fxReq("USD", 1.3, "")); code != http.StatusNoContent {
		t.Fatalf("floor 1.3 USD (120.51 RUB) must reject, got %d", code)
	}
}

func TestFX_PartnerFloorInPartnerCurrency(t *testing.T) {
	snap := fxSnapshot("USD", map[string]float64{"USD": fxUSDRate})
	snap.Partners["key1"].BidFloor = 1.5 // USD -> 139.05 ₽ > 120 ₽
	if code, _ := fxBid(t, snap, fxReq("USD", 0, "")); code != http.StatusNoContent {
		t.Fatalf("partner floor 1.5 USD must reject 120 RUB candidate, got %d", code)
	}
	snap.Partners["key1"].BidFloor = 1.2 // 111.24 ₽
	if code, _ := fxBid(t, snap, fxReq("USD", 0, "")); code != http.StatusOK {
		t.Fatalf("partner floor 1.2 USD must pass, got %d", code)
	}
}

func TestFX_OwnerMarkupChangesRate(t *testing.T) {
	snap := fxSnapshot("USD", map[string]float64{"USD": fxUSDRate})
	m := 10.0
	snap.Candidates[0].FXMarkupPercent = &m // effective 99
	code, r := fxBid(t, snap, fxReq("USD", 1, ""))
	if code != http.StatusOK {
		t.Fatalf("want 200, got %d", code)
	}
	want := roundPrice(120.0 / 99.0) // 1.2121
	if got := r.SeatBid[0].Bid[0].Price; math.Abs(got-want) > 1e-6 {
		t.Fatalf("price with 10%% markup: want %v, got %v", want, got)
	}
}

func TestFX_RUBFallbackWhenSSPTakesRubles(t *testing.T) {
	snap := fxSnapshot("USD", map[string]float64{"USD": fxUSDRate})
	code, r := fxBid(t, snap, fxReq("RUB", 1, ""))
	if code != http.StatusOK || r.Cur != "RUB" {
		t.Fatalf("SSP accepts RUB only: want RUB bid, got %d %q", code, r.Cur)
	}
	if got := r.SeatBid[0].Bid[0].Price; math.Abs(got-120) > 1e-6 {
		t.Fatalf("RUB price untouched: want 120, got %v", got)
	}
}

func TestFX_NoBidWhenCurrencyUnsupported(t *testing.T) {
	// Рублёвый партнёр, SSP принимает только USD.
	snap := fxSnapshot("", map[string]float64{"USD": fxUSDRate})
	if code, _ := fxBid(t, snap, fxReq("USD", 0, "")); code != http.StatusNoContent {
		t.Fatalf("RUB partner + cur=[USD] must no-bid, got %d", code)
	}
}

func TestFX_NoBidWhenRateMissing(t *testing.T) {
	// USD-партнёр, но курса нет — ставка «как будто в рублях» запрещена.
	snap := fxSnapshot("USD", map[string]float64{})
	if code, _ := fxBid(t, snap, fxReq("USD", 0, "")); code != http.StatusNoContent {
		t.Fatalf("missing USD rate must no-bid, got %d", code)
	}
	// bidfloorcur в валюте без курса — тоже no-bid, даже при рублёвом ответе.
	snap = fxSnapshot("", map[string]float64{})
	if code, _ := fxBid(t, snap, fxReq("RUB", 1, "EUR")); code != http.StatusNoContent {
		t.Fatalf("bidfloorcur=EUR without rate must no-bid, got %d", code)
	}
}

func TestFX_BidMetaCarriesCurAndRate(t *testing.T) {
	snap := fxSnapshot("USD", map[string]float64{"USD": fxUSDRate})
	h, mr := newMiniRedisBidHandler(t, snap)
	w := doBid(t, h, fxReq("USD", 1, ""))
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d: %s", w.Code, w.Body.String())
	}
	meta := readBidMeta(t, mr.Keys(), mr.Get)
	if meta["cur"] != "USD" {
		t.Fatalf("bidmeta.cur: want USD, got %v", meta["cur"])
	}
	if r, _ := meta["fx_rate"].(float64); math.Abs(r-fxEffective) > 1e-9 {
		t.Fatalf("bidmeta.fx_rate: want %v, got %v", fxEffective, meta["fx_rate"])
	}
	// Внутренняя цена и резерв — в RUB, не в USD.
	if p, _ := meta["price"].(float64); math.Abs(p-120) > 1e-6 {
		t.Fatalf("bidmeta.price must stay RUB 120, got %v", meta["price"])
	}
	if r, _ := meta["reserve"].(float64); math.Abs(r-0.12) > 1e-9 {
		t.Fatalf("bidmeta.reserve must be RUB 0.12, got %v", meta["reserve"])
	}
}
