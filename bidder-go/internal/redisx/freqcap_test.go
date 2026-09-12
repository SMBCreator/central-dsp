package redisx

// Frequency cap lifecycle (audit R-06): bid → in-flight → win reservation →
// imp confirm; gate formula confirmed + active_reservations < cap.
// Зеркало backend/tests/test_freq_caps.py.

import (
	"context"
	"encoding/json"
	"strconv"
	"testing"
	"time"
)

var fcNow = time.Date(2026, 3, 10, 12, 0, 0, 0, time.UTC)

func fcBid(t *testing.T, c *Client, bidID, dev string, caps []CapWindow) (BidReservation, bool) {
	t.Helper()
	res, ok, err := c.Reserve(context.Background(), fcNow, ReserveInput{
		BidID: bidID, CampaignID: "c1", LiID: "li1", Cost: 0.1, CampBudget: 100,
		DeviceID: dev, Caps: caps,
	})
	if err != nil {
		t.Fatal(err)
	}
	return res, ok
}

func fcInflight(t *testing.T, c *Client) []string {
	t.Helper()
	members, err := c.rdb.ZRange(context.Background(), FreqInflightKey("c1", "dev1"), 0, -1).Result()
	if err != nil {
		t.Fatal(err)
	}
	return members
}

func fcCounter(t *testing.T, c *Client, hours int) int {
	t.Helper()
	v, err := c.rdb.Get(context.Background(), FreqKey("c1", hours, "dev1")).Result()
	if err != nil {
		return 0
	}
	n, _ := strconv.Atoi(v)
	return n
}

func TestFreqCap_InflightBidsCountAgainstCap(t *testing.T) {
	c, _ := newTestClient(t)
	caps := []CapWindow{{Limit: 2, PeriodHours: 24}}
	if _, ok := fcBid(t, c, "b1", "dev1", caps); !ok {
		t.Fatal("b1 must pass")
	}
	if _, ok := fcBid(t, c, "b2", "dev1", caps); !ok {
		t.Fatal("b2 must pass")
	}
	// Ничего не показано, но два бида в полёте: кап 2 занят.
	if _, ok := fcBid(t, c, "b3", "dev1", caps); ok {
		t.Fatal("b3 must be rejected by in-flight reservations")
	}
	if got := fcInflight(t, c); len(got) != 2 {
		t.Fatalf("inflight = %v", got)
	}
	if fcCounter(t, c, 24) != 0 {
		t.Fatal("no impression confirmed yet")
	}
}

func TestFreqCap_LossFreesTheSlot(t *testing.T) {
	c, _ := newTestClient(t)
	caps := []CapWindow{{Limit: 1, PeriodHours: 24}}
	r1, _ := fcBid(t, c, "b1", "dev1", caps)
	if _, ok := fcBid(t, c, "b2", "dev1", caps); ok {
		t.Fatal("cap full")
	}
	if _, err := c.ReleaseBid(context.Background(), r1, "loss"); err != nil {
		t.Fatal(err)
	}
	if _, ok := fcBid(t, c, "b2", "dev1", caps); !ok {
		t.Fatal("loss must free the slot")
	}
}

func TestFreqCap_WinIsReservationNotImpression(t *testing.T) {
	c, _ := newTestClient(t)
	caps := []CapWindow{{Limit: 1, PeriodHours: 24}}
	r1, _ := fcBid(t, c, "b1", "dev1", caps)
	if code, err := c.SettleWin(context.Background(), fcNow, r1, 0.05, false); err != nil || code != 1 {
		t.Fatalf("settle: code=%d err=%v", code, err)
	}
	if fcCounter(t, c, 24) != 0 {
		t.Fatal("win must not increment the counter")
	}
	if got := fcInflight(t, c); len(got) != 1 || got[0] != "w:b1" {
		t.Fatalf("inflight after win = %v, want [w:b1]", got)
	}
	score, err := c.rdb.ZScore(context.Background(), FreqInflightKey("c1", "dev1"), "w:b1").Result()
	if err != nil {
		t.Fatal(err)
	}
	if want := float64(fcNow.Add(WinImpTTL).Unix()); score != want {
		t.Fatalf("reservation deadline %v, want %v", score, want)
	}
	if _, ok := fcBid(t, c, "b2", "dev1", caps); ok {
		t.Fatal("win reservation must still hold the cap")
	}
}

func TestFreqCap_ImpConfirmsAndCounterGetsWindowTTL(t *testing.T) {
	c, mr := newTestClient(t)
	caps := []CapWindow{{Limit: 2, PeriodHours: 1}, {Limit: 5, PeriodHours: 24}, {Limit: 9, PeriodHours: 168}}
	r1, _ := fcBid(t, c, "b1", "dev1", caps)
	if _, err := c.SettleWin(context.Background(), fcNow, r1, 0.05, false); err != nil {
		t.Fatal(err)
	}
	res, err := c.ConfirmImp(context.Background(), r1)
	if err != nil || !res.Counted {
		t.Fatalf("confirm: %+v err=%v", res, err)
	}
	if res.Late {
		t.Fatal("pixel within WinImpTTL must not be flagged late")
	}
	if got := fcInflight(t, c); len(got) != 0 {
		t.Fatalf("reservation must be dropped, got %v", got)
	}
	for _, h := range []int{1, 24, 168} {
		if fcCounter(t, c, h) != 1 {
			t.Fatalf("window %dh counter = %d", h, fcCounter(t, c, h))
		}
		ttl := mr.TTL(FreqKey("c1", h, "dev1"))
		if ttl <= 0 || ttl > time.Duration(h)*time.Hour {
			t.Fatalf("window %dh ttl = %v", h, ttl)
		}
	}
	// Окно 1h с капом 2: одно подтверждённое + одно в полёте = занято.
	if _, ok := fcBid(t, c, "b2", "dev1", caps); !ok {
		t.Fatal("b2 must pass")
	}
	if _, ok := fcBid(t, c, "b3", "dev1", caps); ok {
		t.Fatal("b3 must be rejected: confirmed + inflight == cap")
	}
}

func TestFreqCap_WinReservationExpiresWithoutImpAndIsCounted(t *testing.T) {
	c, _ := newTestClient(t)
	caps := []CapWindow{{Limit: 1, PeriodHours: 24}}
	r1, _ := fcBid(t, c, "b1", "dev1", caps)
	if _, err := c.SettleWin(context.Background(), fcNow, r1, 0.05, false); err != nil {
		t.Fatal(err)
	}
	if _, ok := fcBid(t, c, "b2", "dev1", caps); ok {
		t.Fatal("reserved")
	}
	before := WinWithoutImp.Load()
	// Гейт после истечения WinImpTTL: бронь вычищается, слот свободен.
	later := fcNow.Add(WinImpTTL + time.Second)
	_, ok, err := c.Reserve(context.Background(), later, ReserveInput{
		BidID: "b2", CampaignID: "c1", LiID: "li1", Cost: 0.1, CampBudget: 100, DeviceID: "dev1", Caps: caps,
	})
	if err != nil || !ok {
		t.Fatalf("expired reservation must free the slot: ok=%v err=%v", ok, err)
	}
	if fcCounter(t, c, 24) != 0 {
		t.Fatal("never confirmed → not an impression")
	}
	if WinWithoutImp.Load() != before+1 {
		t.Fatalf("win_without_imp = %d, want %d", WinWithoutImp.Load(), before+1)
	}
}

func TestFreqCap_LateImpAfterExpiredReservationStillCounts(t *testing.T) {
	c, _ := newTestClient(t)
	caps := []CapWindow{{Limit: 3, PeriodHours: 24}}
	r1, _ := fcBid(t, c, "b1", "dev1", caps)
	if _, err := c.SettleWin(context.Background(), fcNow, r1, 0.05, false); err != nil {
		t.Fatal(err)
	}
	c.rdb.Del(context.Background(), FreqInflightKey("c1", "dev1"))
	res, err := c.ConfirmImp(context.Background(), r1)
	if err != nil || !res.Counted {
		t.Fatalf("late pixel must count: %+v %v", res, err)
	}
	if !res.Late {
		t.Fatal("pixel without a reservation must be flagged late (rtb_imp_late_after_reservation_total)")
	}
	if fcCounter(t, c, 24) != 1 {
		t.Fatal("counter must be 1")
	}
}

func TestFreqCap_DuplicateWinDoesNotDoubleReserve(t *testing.T) {
	c, _ := newTestClient(t)
	caps := []CapWindow{{Limit: 5, PeriodHours: 24}}
	r1, _ := fcBid(t, c, "b1", "dev1", caps)
	if code, _ := c.SettleWin(context.Background(), fcNow, r1, 0.05, false); code != 1 {
		t.Fatalf("first settle code %d", code)
	}
	if code, _ := c.SettleWin(context.Background(), fcNow, r1, 0.05, false); code != 0 {
		t.Fatalf("duplicate settle code %d", code)
	}
	if got := fcInflight(t, c); len(got) != 1 {
		t.Fatalf("inflight = %v", got)
	}
}

func TestFreqCap_PartnerWithoutPixelCountsOnWin(t *testing.T) {
	c, _ := newTestClient(t)
	caps := []CapWindow{{Limit: 2, PeriodHours: 24}}
	r1, _ := fcBid(t, c, "b1", "dev1", caps)
	r1.CountOnWin = true
	if _, err := c.SettleWin(context.Background(), fcNow, r1, 0.05, false); err != nil {
		t.Fatal(err)
	}
	if fcCounter(t, c, 24) != 1 {
		t.Fatal("freq_cap_on_win partner must commit on win")
	}
	if got := fcInflight(t, c); len(got) != 0 {
		t.Fatalf("no reservation expected, got %v", got)
	}
	// Поздний пиксель от такого партнёра не удваивает счёт.
	if res, _ := c.ConfirmImp(context.Background(), r1); res.Counted || res.Late {
		t.Fatalf("pixel after count-on-win must neither increment nor be late: %+v", res)
	}
	if fcCounter(t, c, 24) != 1 {
		t.Fatal("counter must stay 1")
	}
}

func TestFreqCap_NoDeviceIsFailOpen(t *testing.T) {
	c, _ := newTestClient(t)
	caps := []CapWindow{{Limit: 1, PeriodHours: 24}}
	r1, ok := fcBid(t, c, "b1", "", caps)
	if !ok {
		t.Fatal("b1")
	}
	if _, ok := fcBid(t, c, "b2", "", caps); !ok {
		t.Fatal("no device must bypass the cap")
	}
	if res, _ := c.ConfirmImp(context.Background(), r1); res.Counted || res.Late {
		t.Fatalf("confirm without device must be a no-op: %+v", res)
	}
}

func TestFreqCap_NoCapsMeansNoFreqKeys(t *testing.T) {
	c, mr := newTestClient(t)
	r1, ok := fcBid(t, c, "b1", "dev1", nil)
	if !ok {
		t.Fatal("b1")
	}
	if mr.Exists(FreqInflightKey("c1", "dev1")) {
		t.Fatal("no caps → no freqinf key")
	}
	if code, err := c.SettleWin(context.Background(), fcNow, r1, 0.05, false); err != nil || code != 1 {
		t.Fatalf("settle without caps: %d %v", code, err)
	}
	if res, _ := c.ConfirmImp(context.Background(), r1); res.Counted || res.Late {
		t.Fatalf("confirm without caps is a no-op: %+v", res)
	}
	for _, k := range mr.Keys() {
		if len(k) >= 5 && k[:5] == "freq:" {
			t.Fatalf("unexpected freq key %s", k)
		}
	}
}

// bidmeta ↔ BidReservation: fdev/fcaps/fwin едут в Python и обратно.
func TestFreqCap_ParseReservationCarriesFreqContext(t *testing.T) {
	raw, _ := json.Marshal(map[string]interface{}{
		"campaign_id": "c", "line_item_id": "li", "advertiser_id": "a", "reserve": 1.5,
		"business_date": "2026-01-01", "has_total": 1, "has_li": 1,
		"fdev": "dev9", "fcaps": [][]int{{3, 24}, {0, 1}, {5, 168}}, "fwin": 1,
	})
	r, ok := ParseReservation(string(raw))
	if !ok {
		t.Fatal("parse")
	}
	if r.DeviceID != "dev9" || !r.CountOnWin || len(r.Caps) != 2 || r.Caps[1].PeriodHours != 168 {
		t.Fatalf("freq context lost: %+v", r)
	}
	// Тот же формат пишет persistBidMeta (fcaps как [limit, hours]).
	out, _ := json.Marshal(r.Caps)
	if string(out) != "[[3,24],[5,168]]" {
		t.Fatalf("fcaps json = %s", out)
	}
}
