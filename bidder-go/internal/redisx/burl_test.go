package redisx

import (
	"context"
	"strconv"
	"testing"
	"time"
)

// R-07: backend (cache.win_pending) переводит бид партнёра с billing_event=burl
// в состояние won, оставляя резерв в pending:bids. luaSettleWin (общий с Python
// байт в байт) должен: won + резерв держится -> конвертировать (1); won без
// резерва -> досписать (2); won_expired -> досписать с кодом late (3);
// повторный settle -> 0. Здесь эмулируем то, что делает Python-скрипт.
func burlSpend(t *testing.T, c *Client, r BidReservation) float64 {
	t.Helper()
	v, err := c.rdb.Get(context.Background(), SpendKey("camp", r.CampaignID, r.BusinessDate)).Result()
	if err != nil {
		return 0
	}
	f, _ := strconv.ParseFloat(v, 64)
	return f
}

func markWon(t *testing.T, c *Client, r BidReservation, state string) {
	t.Helper()
	if err := c.rdb.Set(context.Background(), BidStateKey(r.BidID), state, BidStateTTL).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.rdb.ZAdd(context.Background(), PendingBurlKey, zmember(r.BidID, fcNow.Add(10*time.Minute))).Err(); err != nil {
		t.Fatal(err)
	}
}

func TestSettleWin_WonWithReserveConverts(t *testing.T) {
	c, _ := newTestClient(t)
	r, ok := fcBid(t, c, "b1", "", nil)
	if !ok {
		t.Fatal("bid must pass")
	}
	markWon(t, c, r, "won")
	code, err := c.SettleWin(context.Background(), fcNow, r, 0.06, false)
	if err != nil || code != 1 {
		t.Fatalf("won+reserve -> convert: code=%d err=%v", code, err)
	}
	if got := burlSpend(t, c, r); got < 0.059 || got > 0.061 {
		t.Fatalf("spend must be clearing 0.06, got %v", got)
	}
	if n, _ := c.rdb.ZScore(context.Background(), PendingBurlKey, "b1").Result(); n != 0 {
		t.Fatalf("pending:burl must be cleared, score=%v", n)
	}
	if st, _ := c.rdb.Get(context.Background(), BidStateKey("b1")).Result(); st != "win" {
		t.Fatalf("state must be win, got %q", st)
	}
	if code, _ := c.SettleWin(context.Background(), fcNow, r, 0.06, false); code != 0 {
		t.Fatalf("duplicate settle must be 0, got %d", code)
	}
}

func TestSettleWin_WonWithoutReserveRecharges(t *testing.T) {
	c, _ := newTestClient(t)
	r, _ := fcBid(t, c, "b1", "", nil)
	markWon(t, c, r, "won")
	// резерв уже снят (sweeper убрал бид из pending:bids)
	if err := c.rdb.ZRem(context.Background(), PendingBidsKey, "b1").Err(); err != nil {
		t.Fatal(err)
	}
	before := burlSpend(t, c, r)
	code, err := c.SettleWin(context.Background(), fcNow, r, 0.06, false)
	if err != nil || code != 2 {
		t.Fatalf("won without reserve -> recharge: code=%d err=%v", code, err)
	}
	if got := burlSpend(t, c, r); got < before+0.059 {
		t.Fatalf("clearing must be charged on top: before=%v after=%v", before, got)
	}
}

func TestSettleWin_WonExpiredIsLate(t *testing.T) {
	c, _ := newTestClient(t)
	r, _ := fcBid(t, c, "b1", "", nil)
	markWon(t, c, r, "won_expired")
	_ = c.rdb.ZRem(context.Background(), PendingBidsKey, "b1").Err()
	code, err := c.SettleWin(context.Background(), fcNow, r, 0.06, false)
	if err != nil || code != 3 {
		t.Fatalf("won_expired -> late recharge: code=%d err=%v", code, err)
	}
}

func TestReservationKeys_EndWithPendingBurl(t *testing.T) {
	r := BidReservation{BidID: "b", CampaignID: "c", LineItemID: "li", BusinessDate: "2026-03-10"}
	keys := r.keys()
	if len(keys) != 13 || keys[12] != PendingBurlKey || keys[1] != PendingBidsKey {
		t.Fatalf("keys layout changed: %v", keys)
	}
}
