package redisx

import (
	"context"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Audit M-05/M-06: per-bid reservation lifecycle.
//
// Инварианты:
//   - сумма обязательств никогда не превышает доступный баланс рекламодателя
//     и остаток общего бюджета, даже при конкурентных бидах нескольких кампаний;
//   - loss/expire возвращают резерв ровно один раз; повтор, out-of-order и
//     loss-после-win ничего не двигают;
//   - win после loss/expire досписывает clearing, а не возвращает разницу;
//   - sweeper безопасен при двух инстансах.

func getF(t *testing.T, c *Client, key string) float64 {
	t.Helper()
	raw, err := c.Raw().Get(context.Background(), key).Result()
	if err != nil {
		return 0
	}
	f, _ := strconv.ParseFloat(raw, 64)
	return f
}

func approx(a, b float64) bool { d := a - b; return d < 1e-6 && d > -1e-6 }

func mkInput(bid, camp, li, adv string, cost, campBudget, advBalance, totalRemaining float64, hasTotal bool) ReserveInput {
	return ReserveInput{
		BidID: bid, CampaignID: camp, LiID: li, AdvertiserID: adv,
		Cost: cost, CampBudget: campBudget, LiBudget: 0,
		AdvBalance: advBalance, TotalRemaining: totalRemaining, HasTotal: hasTotal,
	}
}

// Баланс 1.0 руб, две кампании с дневным бюджетом 1000 руб каждая, 400
// конкурентных бидов по 0.01 -> ровно 100 резервов; avail:adv == 0, res:adv == 1.
func TestReserve_AdvertiserBalanceBoundsConcurrentCampaigns(t *testing.T) {
	c, _ := newTestClient(t)
	ctx := context.Background()
	now := time.Now()
	var ok int64
	var mu sync.Mutex
	var wg sync.WaitGroup
	for i := 0; i < 400; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			camp := "camp-A"
			if i%2 == 1 {
				camp = "camp-B"
			}
			_, allowed, err := c.Reserve(ctx, now, mkInput("bid-"+strconv.Itoa(i), camp, "li-"+camp, "adv-1", 0.01, 1000, 1.0, 0, false))
			if err != nil {
				t.Error(err)
				return
			}
			if allowed {
				mu.Lock()
				ok++
				mu.Unlock()
			}
		}(i)
	}
	wg.Wait()
	if ok != 100 {
		t.Fatalf("allowed=%d, want exactly 100 (balance 1.0 / 0.01)", ok)
	}
	if v := getF(t, c, AvailAdvKey("adv-1")); !approx(v, 0) {
		t.Fatalf("avail:adv must be 0, got %v", v)
	}
	if v := getF(t, c, ResAdvKey("adv-1")); !approx(v, 1.0) {
		t.Fatalf("res:adv must equal committed 1.0, got %v", v)
	}
	if n := c.PendingCount(ctx); n != 100 {
		t.Fatalf("pending:bids=%d, want 100", n)
	}
	// Daily counters of the two campaigns sum to the same 1.0 — nothing leaked on rollback.
	day := BusinessDate(now)
	sum := getF(t, c, SpendKey("camp", "camp-A", day)) + getF(t, c, SpendKey("camp", "camp-B", day))
	if !approx(sum, 1.0) {
		t.Fatalf("daily spend sum=%v, want 1.0", sum)
	}
}

// Остаток total budget 0.05 при большом балансе — ровно 5 резервов по 0.01.
func TestReserve_TotalBudgetRemainingBounds(t *testing.T) {
	c, _ := newTestClient(t)
	ctx := context.Background()
	now := time.Now()
	allowed := 0
	for i := 0; i < 20; i++ {
		_, ok, err := c.Reserve(ctx, now, mkInput("t-"+strconv.Itoa(i), "camp-T", "li-T", "adv-T", 0.01, 1000, 1e6, 0.05, true))
		if err != nil {
			t.Fatal(err)
		}
		if ok {
			allowed++
		}
	}
	if allowed != 5 {
		t.Fatalf("allowed=%d, want 5", allowed)
	}
	// Rollback on total-exhausted must have restored adv availability exactly.
	if v := getF(t, c, AvailAdvKey("adv-T")); !approx(v, 1e6-0.05) {
		t.Fatalf("avail:adv=%v, want %v", v, 1e6-0.05)
	}
	if v := getF(t, c, AvailCampKey("camp-T")); !approx(v, 0) {
		t.Fatalf("avail:camp=%v, want 0", v)
	}
}

// loss: один возврат на 50 конкурентных повторов; win после loss досписывает.
func TestReleaseBid_IdempotentAndWinAfterLossRecharges(t *testing.T) {
	c, _ := newTestClient(t)
	ctx := context.Background()
	now := time.Now()
	res, ok, err := c.Reserve(ctx, now, mkInput("bid-x", "camp-X", "li-X", "adv-X", 0.02, 100, 10, 5, true))
	if err != nil || !ok {
		t.Fatalf("reserve ok=%v err=%v", ok, err)
	}
	var released int64
	var mu sync.Mutex
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r, err := c.ReleaseBid(ctx, res, "loss")
			if err != nil {
				t.Error(err)
			}
			if r {
				mu.Lock()
				released++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if released != 1 {
		t.Fatalf("released %d times, want 1", released)
	}
	day := BusinessDate(now)
	if v := getF(t, c, SpendKey("camp", "camp-X", day)); !approx(v, 0) {
		t.Fatalf("spend after loss=%v", v)
	}
	if v := getF(t, c, AvailAdvKey("adv-X")); !approx(v, 10) {
		t.Fatalf("avail:adv after loss=%v, want 10", v)
	}
	if v := getF(t, c, AvailCampKey("camp-X")); !approx(v, 5) {
		t.Fatalf("avail:camp after loss=%v, want 5", v)
	}
	if v := getF(t, c, ResAdvKey("adv-X")); !approx(v, 0) {
		t.Fatalf("res:adv after loss=%v, want 0", v)
	}
	if n := c.PendingCount(ctx); n != 0 {
		t.Fatalf("pending after loss=%d", n)
	}
	// Late win (SSP out-of-order): clearing 0.015 is charged anew, not refunded.
	code, err := c.SettleWin(ctx, now, res, 0.015, false)
	if err != nil || code != 2 {
		t.Fatalf("settle after loss code=%d err=%v, want 2", code, err)
	}
	if v := getF(t, c, SpendKey("camp", "camp-X", day)); !approx(v, 0.015) {
		t.Fatalf("spend after late win=%v, want 0.015", v)
	}
	if v := getF(t, c, AvailAdvKey("adv-X")); !approx(v, 10-0.015) {
		t.Fatalf("avail:adv after late win=%v", v)
	}
	// Loss after win: nothing moves.
	r, _ := c.ReleaseBid(ctx, res, "loss")
	if r {
		t.Fatal("loss after win must be a no-op")
	}
	if v := getF(t, c, SpendKey("camp", "camp-X", day)); !approx(v, 0.015) {
		t.Fatalf("loss after win changed spend: %v", v)
	}
}

// win: резерв 0.02 -> clearing 0.012: разница возвращается на все ключи, второй win = 0.
func TestSettleWin_ConvertsReserveToClearingOnce(t *testing.T) {
	c, _ := newTestClient(t)
	ctx := context.Background()
	now := time.Now()
	res, ok, _ := c.Reserve(ctx, now, mkInput("bid-w", "camp-W", "li-W", "adv-W", 0.02, 100, 10, 5, true))
	if !ok {
		t.Fatal("reserve")
	}
	code, err := c.SettleWin(ctx, now, res, 0.012, false)
	if err != nil || code != 1 {
		t.Fatalf("code=%d err=%v", code, err)
	}
	day := BusinessDate(now)
	if v := getF(t, c, SpendKey("camp", "camp-W", day)); !approx(v, 0.012) {
		t.Fatalf("spend=%v, want 0.012", v)
	}
	if v := getF(t, c, AvailAdvKey("adv-W")); !approx(v, 10-0.012) {
		t.Fatalf("avail:adv=%v", v)
	}
	if v := getF(t, c, AvailCampKey("camp-W")); !approx(v, 5-0.012) {
		t.Fatalf("avail:camp=%v", v)
	}
	if v := getF(t, c, ResAdvKey("adv-W")); !approx(v, 0) {
		t.Fatalf("res:adv=%v", v)
	}
	code, _ = c.SettleWin(ctx, now, res, 0.012, false)
	if code != 0 {
		t.Fatalf("duplicate win code=%d, want 0", code)
	}
	if v := getF(t, c, SpendKey("camp", "camp-W", day)); !approx(v, 0.012) {
		t.Fatalf("duplicate win changed spend: %v", v)
	}
}

// Sweeper: истёкший резерв -> expired + refund; два инстанса не возвращают дважды;
// бид без bidmeta считается orphan и снимается из pending.
func TestSweepExpired_RefundsOnceAcrossInstances(t *testing.T) {
	c, _ := newTestClient(t)
	ctx := context.Background()
	now := time.Now()
	res, ok, _ := c.Reserve(ctx, now, ReserveInput{
		BidID: "bid-s", CampaignID: "camp-S", LiID: "li-S", AdvertiserID: "adv-S",
		Cost: 0.03, CampBudget: 100, AdvBalance: 10, ReserveTTL: 10 * time.Second,
	})
	if !ok {
		t.Fatal("reserve")
	}
	// bidmeta as persistBidMeta writes it
	meta := `{"bid_id":"bid-s","campaign_id":"camp-S","line_item_id":"li-S","advertiser_id":"adv-S",` +
		`"price":30,"reserve":0.03,"has_total":0,"has_li":0,"is_test":0,"business_date":"` + res.BusinessDate + `"}`
	c.Raw().Set(ctx, "bidmeta:bid-s", meta, time.Hour)
	// orphan: pending entry without bidmeta
	c.Raw().ZAdd(ctx, PendingBidsKey, zmember("ghost", now.Add(-time.Minute)))

	// Before deadline: nothing expires.
	e, o, err := c.SweepExpired(ctx, now.Add(5*time.Second), 100)
	if err != nil || e != 0 || o != 1 {
		t.Fatalf("early sweep expired=%d orphans=%d err=%v", e, o, err)
	}
	// Two "instances" sweep after the deadline concurrently.
	var wg sync.WaitGroup
	total := make([]int, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			e, _, err := c.SweepExpired(ctx, now.Add(20*time.Second), 100)
			if err != nil {
				t.Error(err)
			}
			total[i] = e
		}(i)
	}
	wg.Wait()
	if total[0]+total[1] != 1 {
		t.Fatalf("expired across instances=%d, want 1", total[0]+total[1])
	}
	day := BusinessDate(now)
	if v := getF(t, c, SpendKey("camp", "camp-S", day)); !approx(v, 0) {
		t.Fatalf("spend after expire=%v", v)
	}
	if v := getF(t, c, AvailAdvKey("adv-S")); !approx(v, 10) {
		t.Fatalf("avail:adv after expire=%v", v)
	}
	if st, _ := c.Raw().Get(ctx, BidStateKey("bid-s")).Result(); st != "expired" {
		t.Fatalf("bidstate=%q, want expired", st)
	}
	if n := c.PendingCount(ctx); n != 0 {
		t.Fatalf("pending=%d, want 0", n)
	}
}

// Смена суток: резерв вчера, loss сегодня — возвращается во вчерашний ключ,
// сегодняшний счётчик не трогается.
func TestReleaseBid_RefundsTheBidsOwnDay(t *testing.T) {
	c, _ := newTestClient(t)
	ctx := context.Background()
	yesterday := time.Date(2026, 9, 5, 20, 0, 0, 0, time.UTC) // 23:00 MSK 05.09
	today := time.Date(2026, 9, 5, 21, 30, 0, 0, time.UTC)    // 00:30 MSK 06.09
	res, ok, _ := c.Reserve(ctx, yesterday, mkInput("bid-y", "camp-Y", "li-Y", "adv-Y", 0.05, 100, 10, 0, false))
	if !ok {
		t.Fatal("reserve")
	}
	if _, ok, _ := c.Reserve(ctx, today, mkInput("bid-z", "camp-Y", "li-Y", "adv-Y", 0.07, 100, 10, 0, false)); !ok {
		t.Fatal("reserve today")
	}
	if r, _ := c.ReleaseBid(ctx, res, "loss"); !r {
		t.Fatal("release")
	}
	if v := getF(t, c, SpendKey("camp", "camp-Y", "2026-09-05")); !approx(v, 0) {
		t.Fatalf("yesterday spend=%v, want 0", v)
	}
	if v := getF(t, c, SpendKey("camp", "camp-Y", "2026-09-06")); !approx(v, 0.07) {
		t.Fatalf("today spend=%v, want 0.07 untouched", v)
	}
	if v := getF(t, c, AvailAdvKey("adv-Y")); !approx(v, 10-0.07) {
		t.Fatalf("avail:adv=%v", v)
	}
}

// Legacy GateAndReserve (без advertiser) не создаёт avail:*-ключей.
func TestGateAndReserve_LegacyPathHasNoAdvertiserKeys(t *testing.T) {
	c, _ := newTestClient(t)
	ctx := context.Background()
	if ok, _ := c.GateAndReserve(ctx, "camp-legacy", "li-legacy", 1, 100, 0, "", nil); !ok {
		t.Fatal("legacy reserve")
	}
	if n, _ := c.Raw().Exists(ctx, AvailAdvKey("-"), ResAdvKey("-")).Result(); n != 0 {
		t.Fatalf("legacy path must not touch advertiser keys, exists=%d", n)
	}
	if n := c.PendingCount(ctx); n != 0 {
		t.Fatalf("legacy path must not track pending, got %d", n)
	}
}

func zmember(id string, deadline time.Time) redis.Z {
	return redis.Z{Score: float64(deadline.Unix()), Member: id}
}
