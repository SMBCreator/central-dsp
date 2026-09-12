package redisx

// Audit M-04: одна граница дневного бюджета — полночь Europe/Moscow.
// Ключи spend:* датированы бизнес-днём, поэтому:
//   * 20:59 UTC и 21:00 UTC одного календарного дня UTC — РАЗНЫЕ бизнес-дни;
//   * 21:00 UTC и 23:59 UTC и 00:00 UTC следующих суток UTC — ОДИН бизнес-день;
//   * loss после полуночи возвращает резерв в ключ дня ставки, не нового дня.

import (
	"context"
	"testing"
	"time"
)

func TestBusinessDate_MoscowMidnightIsTheOnlyBoundary(t *testing.T) {
	cases := []struct {
		utc  string
		want string
	}{
		{"2026-09-06T20:59:59Z", "2026-09-06"},
		{"2026-09-06T21:00:00Z", "2026-09-07"}, // 00:00 MSK — новый бизнес-день
		{"2026-09-06T23:59:59Z", "2026-09-07"},
		{"2026-09-07T00:00:00Z", "2026-09-07"}, // полночь UTC ничего не меняет
		{"2026-09-07T20:59:59Z", "2026-09-07"},
	}
	for _, c := range cases {
		now, _ := time.Parse(time.RFC3339, c.utc)
		if got := BusinessDate(now); got != c.want {
			t.Errorf("BusinessDate(%s) = %s, want %s", c.utc, got, c.want)
		}
	}
}

func TestSecondsUntilBusinessMidnight_TTLCoversWholeBusinessDay(t *testing.T) {
	now, _ := time.Parse(time.RFC3339, "2026-09-06T21:00:00Z") // 00:00 MSK
	got := secondsUntilBusinessMidnight(now)
	if want := int64(24*3600) + keyGraceSeconds; got != want {
		t.Fatalf("ttl at business midnight = %d, want %d", got, want)
	}
	late, _ := time.Parse(time.RFC3339, "2026-09-06T20:59:59Z") // 23:59:59 MSK
	if got := secondsUntilBusinessMidnight(late); got != 60+keyGraceSeconds {
		t.Fatalf("ttl one second before midnight = %d, want floor 60 + grace", got)
	}
}

// Audit R-03: even pacing считается по бизнес-дню (МСК), не по UTC — иначе в
// 21:00 UTC (00:00 МСК) кампания получила бы 87.5% дневного бюджета сразу.
// Числа — те же, что в backend/tests/test_business_day.py.
func TestEvenPacingBudget_MirrorsPython(t *testing.T) {
	at := func(s string) time.Time { n, _ := time.Parse(time.RFC3339, s); return n }
	near := func(a, b float64) bool { d := a - b; return d < 1e-9 && d > -1e-9 }

	if f := BusinessDayFraction(at("2026-09-06T21:00:00Z")); f != 0 {
		t.Fatalf("00:00 MSK fraction = %v, want 0", f)
	}
	if f := BusinessDayFraction(at("2026-09-07T09:00:00Z")); !near(f, 0.5) {
		t.Fatalf("12:00 MSK fraction = %v, want 0.5", f)
	}
	if f := BusinessDayFraction(at("2026-09-07T20:59:59Z")); !near(f, 86399.0/86400) {
		t.Fatalf("23:59:59 MSK fraction = %v", f)
	}

	if b := EvenPacingBudget(1000, at("2026-09-06T21:00:00Z")); !near(b, 100) {
		t.Fatalf("budget at business midnight = %v, want burst 100", b)
	}
	if b := EvenPacingBudget(1000, at("2026-09-07T09:00:00Z")); !near(b, 600) {
		t.Fatalf("budget at noon MSK = %v, want 600", b)
	}
	if b := EvenPacingBudget(1000, at("2026-09-07T20:00:00Z")); b != 1000 {
		t.Fatalf("budget at 23:00 MSK = %v, want cap 1000", b)
	}
	if b := EvenPacingBudget(0, at("2026-09-07T09:00:00Z")); b != 0 {
		t.Fatalf("zero budget must stay zero, got %v", b)
	}
}

func TestGateAndReserve_NewBusinessDayReopensBudgetExactlyOnce(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()

	dayEnd, _ := time.Parse(time.RFC3339, "2026-09-06T20:59:00Z")   // 23:59 MSK
	dayStart, _ := time.Parse(time.RFC3339, "2026-09-06T21:00:00Z") // 00:00 MSK
	utcMidnight, _ := time.Parse(time.RFC3339, "2026-09-07T00:00:00Z")

	// Бюджет 10, цена 10: одна ставка исчерпывает день.
	ok, err := c.GateAndReserveAt(ctx, dayEnd, "camp-D", "li-D", 10, 10, 0, "", nil)
	if err != nil || !ok {
		t.Fatalf("first bid of the day must pass: ok=%v err=%v", ok, err)
	}
	if ok, _ := c.GateAndReserveAt(ctx, dayEnd, "camp-D", "li-D", 10, 10, 0, "", nil); ok {
		t.Fatal("budget exhausted for 2026-09-06, second bid must be rejected")
	}

	// 00:00 MSK — новый бизнес-день, бюджет открывается один раз.
	if ok, _ := c.GateAndReserveAt(ctx, dayStart, "camp-D", "li-D", 10, 10, 0, "", nil); !ok {
		t.Fatal("new business day must reopen the budget")
	}
	// 00:00 UTC той же ночи — тот же бизнес-день, НЕ второе открытие.
	if ok, _ := c.GateAndReserveAt(ctx, utcMidnight, "camp-D", "li-D", 10, 10, 0, "", nil); ok {
		t.Fatal("UTC midnight must not reopen the budget a second time (двойной сброс)")
	}

	if !mr.Exists(SpendKey("camp", "camp-D", "2026-09-06")) || !mr.Exists(SpendKey("camp", "camp-D", "2026-09-07")) {
		t.Fatal("both business days must have their own dated key")
	}
}

func TestReleaseGate_LateLossRefundsTheBidsDayNotToday(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()

	yesterday, _ := time.Parse(time.RFC3339, "2026-09-06T20:00:00Z") // 23:00 MSK 06.09
	today, _ := time.Parse(time.RFC3339, "2026-09-06T21:30:00Z")     // 00:30 MSK 07.09

	if ok, _ := c.GateAndReserveAt(ctx, yesterday, "camp-R", "li-R", 4, 100, 0, "", nil); !ok {
		t.Fatal("reserve yesterday")
	}
	if ok, _ := c.GateAndReserveAt(ctx, today, "camp-R", "li-R", 6, 100, 0, "", nil); !ok {
		t.Fatal("reserve today")
	}

	// Loss вчерашней ставки приходит после полуночи: уменьшает ВЧЕРА, не сегодня.
	if err := c.ReleaseGate(ctx, "camp-R", "li-R", 4, BusinessDate(yesterday)); err != nil {
		t.Fatal(err)
	}
	if got := floatKey(t, mr, SpendKey("camp", "camp-R", "2026-09-06")); !approxEqual(got, 0) {
		t.Fatalf("yesterday spend = %v, want 0", got)
	}
	if got := floatKey(t, mr, SpendKey("camp", "camp-R", "2026-09-07")); !approxEqual(got, 6) {
		t.Fatalf("today spend = %v, want 6 (late loss must not touch the new day)", got)
	}
}

func TestReleaseGate_EmptyDayDefaultsToToday(t *testing.T) {
	c, mr := newTestClient(t)
	ctx := context.Background()
	if ok, _ := c.GateAndReserve(ctx, "camp-E", "li-E", 3, 100, 0, "", nil); !ok {
		t.Fatal("reserve")
	}
	if err := c.ReleaseGate(ctx, "camp-E", "li-E", 3, ""); err != nil {
		t.Fatal(err)
	}
	if got := floatKey(t, mr, SpendKey("camp", "camp-E", BusinessDate(time.Now()))); !approxEqual(got, 0) {
		t.Fatalf("spend = %v, want 0", got)
	}
}
