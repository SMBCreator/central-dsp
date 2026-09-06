package redisx

// Инварианты денег (аудит §5) для единственного бюджетного гейта биддера.
// Lua-скрипт общий с backend/cache.py (_GATE_LUA); здесь проверяем не текст,
// а поведение под конкуренцией на miniredis: перерасход 0, откат без утечки,
// частотные окна read-only, release возвращает ровно то, что зарезервировали.

import (
	"context"
	"math"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
)

func newTestClient(t *testing.T) (*Client, *miniredis.Miniredis) {
	t.Helper()
	mr := miniredis.RunT(t)
	c, err := New("redis://" + mr.Addr() + "/0")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c, mr
}

func floatKey(t *testing.T, mr *miniredis.Miniredis, key string) float64 {
	t.Helper()
	raw, err := mr.Get(key)
	if err != nil {
		return 0
	}
	f, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		t.Fatalf("key %s is not a float: %q", key, raw)
	}
	return f
}

func approxEqual(a, b float64) bool { return math.Abs(a-b) < 1e-6 }

// 1000 параллельных бидов по 1 руб на дневной бюджет кампании 100 руб:
// ровно 100 разрешений, spend:camp ровно 100, ни копейки сверх.
// today — бизнес-дата (Europe/Moscow) на момент прогона: ключи spend:* датированы.
var today = BusinessDate(time.Now())

func TestGateAndReserve_ConcurrentNoOverspend(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()
	const (
		bids       = 1000
		cost       = 1.0
		campBudget = 100.0
	)
	var allowed atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < bids; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, err := c.GateAndReserve(ctx, "camp-1", "li-1", cost, campBudget, 0, "", nil)
			if err != nil {
				t.Errorf("gate: %v", err)
				return
			}
			if ok {
				allowed.Add(1)
			}
		}()
	}
	wg.Wait()

	if got := allowed.Load(); got != int64(campBudget/cost) {
		t.Fatalf("allowed = %d, want %d", got, int64(campBudget/cost))
	}
	if spent := floatKey(t, mr, SpendKey("camp", "camp-1", today)); !approxEqual(spent, campBudget) {
		t.Fatalf("spend:camp = %v, want %v (rejected bids must roll back fully)", spent, campBudget)
	}
	if mr.TTL(SpendKey("camp", "camp-1", today)) <= 0 {
		t.Fatal("spend:camp must carry a TTL until UTC midnight")
	}
}

// Бюджет группы (30) уже бюджета кампании (100): срабатывает нижний лимит,
// и после отката оба ключа согласованы (camp == li == 30).
func TestGateAndReserve_LineItemBudgetBoundsAndRollsBackBoth(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()
	var allowed atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < 500; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, err := c.GateAndReserve(ctx, "camp-2", "li-2", 1, 100, 30, "", nil)
			if err == nil && ok {
				allowed.Add(1)
			}
		}()
	}
	wg.Wait()

	if got := allowed.Load(); got != 30 {
		t.Fatalf("allowed = %d, want 30", got)
	}
	camp := floatKey(t, mr, SpendKey("camp", "camp-2", today))
	li := floatKey(t, mr, SpendKey("li", "li-2", today))
	if !approxEqual(camp, 30) || !approxEqual(li, 30) {
		t.Fatalf("camp=%v li=%v, want both 30: li-rejection must refund camp too", camp, li)
	}
}

// Дробная стоимость (CPM 12.34 → 0.01234 руб) не должна давать дрейф:
// после N разрешений ключ равен N*cost с плавающей точностью.
func TestGateAndReserve_FractionalCostNoDrift(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()
	const cost = 0.01234
	const budget = 1.0 // ≈ 81 показ
	var allowed atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < 300; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, err := c.GateAndReserve(ctx, "camp-3", "li-3", cost, budget, 0, "", nil)
			if err == nil && ok {
				allowed.Add(1)
			}
		}()
	}
	wg.Wait()
	want := float64(allowed.Load()) * cost
	if want > budget+1e-9 {
		t.Fatalf("reserved %v > budget %v", want, budget)
	}
	if spent := floatKey(t, mr, SpendKey("camp", "camp-3", today)); math.Abs(spent-want) > 1e-6 {
		t.Fatalf("spend:camp=%v, want %v", spent, want)
	}
}

// Частотное окно только читается: при достигнутом капе ответ 0, а spend-ключи
// не тронуты (кап инкрементит бэкенд на подтверждённом показе).
func TestGateAndReserve_FreqCapReadOnly(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()
	mr.Set("freq:camp-4:24h:dev-1", "3")

	ok, err := c.GateAndReserve(ctx, "camp-4", "li-4", 1, 100, 0, "dev-1",
		[]CapWindow{{Limit: 3, PeriodHours: 24}})
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatal("cap reached must reject")
	}
	if mr.Exists(SpendKey("camp", "camp-4", today)) || mr.Exists(SpendKey("li", "li-4", today)) {
		t.Fatal("rejected by cap must not touch spend keys")
	}
	if v, _ := mr.Get("freq:camp-4:24h:dev-1"); v != "3" {
		t.Fatalf("freq counter changed by gate: %q", v)
	}

	// Ниже капа — проходит, кап всё равно не инкрементится.
	mr.Set("freq:camp-4:24h:dev-1", "2")
	ok, _ = c.GateAndReserve(ctx, "camp-4", "li-4", 1, 100, 0, "dev-1",
		[]CapWindow{{Limit: 3, PeriodHours: 24}})
	if !ok {
		t.Fatal("below cap must pass")
	}
	if v, _ := mr.Get("freq:camp-4:24h:dev-1"); v != "2" {
		t.Fatalf("gate must not increment freq counter, got %q", v)
	}
}

// Без device_id кап не применяется (как в Python: limit=0), иначе один
// анонимный пул «-» съел бы кап для всех.
func TestGateAndReserve_NoDeviceDisablesCap(t *testing.T) {
	c, mr := newTestClient(t)
	mr.Set("freq:camp-5:24h:-", "99")
	ok, err := c.GateAndReserve(context.Background(), "camp-5", "li-5", 1, 100, 0, "",
		[]CapWindow{{Limit: 1, PeriodHours: 24}})
	if err != nil || !ok {
		t.Fatalf("no device_id must bypass freq cap, ok=%v err=%v", ok, err)
	}
}

// Нулевой/отрицательный бюджет кампании — отказ без похода в Redis.
func TestGateAndReserve_ZeroBudgetShortCircuits(t *testing.T) {
	c, mr := newTestClient(t)
	ok, err := c.GateAndReserve(context.Background(), "camp-6", "li-6", 1, 0, 0, "", nil)
	if err != nil || ok {
		t.Fatalf("zero budget: ok=%v err=%v", ok, err)
	}
	if len(mr.Keys()) != 0 {
		t.Fatalf("zero budget must not write keys, got %v", mr.Keys())
	}
}

// Release возвращает ровно зарезервированное на оба ключа; после полного
// возврата гейт снова пропускает.
func TestReleaseGate_RefundsExactlyAndReopensGate(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()
	for i := 0; i < 5; i++ {
		if ok, _ := c.GateAndReserve(ctx, "camp-7", "li-7", 2, 10, 10, "", nil); !ok {
			t.Fatalf("reserve %d failed", i)
		}
	}
	if ok, _ := c.GateAndReserve(ctx, "camp-7", "li-7", 2, 10, 10, "", nil); ok {
		t.Fatal("budget exhausted, must reject")
	}
	if err := c.ReleaseGate(ctx, "camp-7", "li-7", 2, ""); err != nil {
		t.Fatal(err)
	}
	if camp, li := floatKey(t, mr, SpendKey("camp", "camp-7", today)), floatKey(t, mr, SpendKey("li", "li-7", today)); !approxEqual(camp, 8) || !approxEqual(li, 8) {
		t.Fatalf("after release camp=%v li=%v, want 8/8", camp, li)
	}
	if ok, _ := c.GateAndReserve(ctx, "camp-7", "li-7", 2, 10, 10, "", nil); !ok {
		t.Fatal("released reservation must reopen the gate")
	}
}

// Регрессия: группа без дневного лимита (li_budget=0) — гейт не трогает
// spend:li, значит и release не должен создавать его отрицательным.
func TestReleaseGate_NoLineItemBudgetNeverGoesNegative(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()
	for i := 0; i < 20; i++ {
		if ok, _ := c.GateAndReserve(ctx, "camp-9", "li-9", 1, 1000, 0, "", nil); !ok {
			t.Fatal("reserve failed")
		}
	}
	for i := 0; i < 20; i++ {
		if err := c.ReleaseGate(ctx, "camp-9", "li-9", 1, ""); err != nil {
			t.Fatal(err)
		}
	}
	if mr.Exists(SpendKey("li", "li-9", today)) {
		t.Fatalf("spend:li must not be created by release, got %v", floatKey(t, mr, SpendKey("li", "li-9", today)))
	}
	if got := floatKey(t, mr, SpendKey("camp", "camp-9", today)); !approxEqual(got, 0) {
		t.Fatalf("spend:camp = %v, want 0", got)
	}
	// Даже лишний release (SSP прислал loss на бид, резерв которого уже
	// сгорел) не уводит счётчик ниже нуля и не снимает TTL.
	if err := c.ReleaseGate(ctx, "camp-9", "li-9", 1, ""); err != nil {
		t.Fatal(err)
	}
	if got := floatKey(t, mr, SpendKey("camp", "camp-9", today)); got < 0 {
		t.Fatalf("spend:camp went negative: %v", got)
	}
	if mr.TTL(SpendKey("camp", "camp-9", today)) <= 0 {
		t.Fatal("clamping to 0 must keep the daily TTL")
	}
}

// Регрессия: loss после полуночи (ключ истёк) не должен засеять новый день
// отрицательным значением.
func TestReleaseGate_AfterKeyExpiryIsNoop(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()
	if ok, _ := c.GateAndReserve(ctx, "camp-10", "li-10", 1, 1000, 1000, "", nil); !ok {
		t.Fatal("reserve failed")
	}
	mr.FastForward(48 * time.Hour) // ключи spend:* истекли
	if mr.Exists(SpendKey("camp", "camp-10", today)) {
		t.Fatal("precondition: key must have expired")
	}
	if err := c.ReleaseGate(ctx, "camp-10", "li-10", 1, ""); err != nil {
		t.Fatal(err)
	}
	if mr.Exists(SpendKey("camp", "camp-10", today)) || mr.Exists(SpendKey("li", "li-10", today)) {
		t.Fatalf("release after expiry must not recreate keys: %v", mr.Keys())
	}
}

// 400 параллельных «бид → loss» (release всегда после своего reserve, как в
// жизни) на общих ключах не теряют обновлений: итог ровно 0 на обоих ключах,
// а во время гонки ни один reserve не отклонён (бюджет заведомо большой).
func TestGateAndReserve_ParallelReserveThenReleaseNetsToZero(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()
	const cost = 0.37
	var rejected atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < 400; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, err := c.GateAndReserve(ctx, "camp-8", "li-8", cost, 1e9, 1e9, "", nil)
			if err != nil {
				t.Errorf("reserve: %v", err)
				return
			}
			if !ok {
				rejected.Add(1)
				return
			}
			if err := c.ReleaseGate(ctx, "camp-8", "li-8", cost, ""); err != nil {
				t.Errorf("release: %v", err)
			}
		}()
	}
	wg.Wait()
	if rejected.Load() != 0 {
		t.Fatalf("%d reserves rejected under a huge budget", rejected.Load())
	}
	for _, k := range []string{SpendKey("camp", "camp-8", today), SpendKey("li", "li-8", today)} {
		if got := floatKey(t, mr, k); !approxEqual(got, 0) {
			t.Fatalf("%s = %v, want 0 after every reservation was released", k, got)
		}
	}
}
