package rtb

// Инварианты денег (аудит §5) для /rtb/loss: повтор lurl от SSP освобождает
// резерв ровно один раз; test=1 не трогает деньги; без bidmeta — no-op.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/redisx"
)

var today = redisx.BusinessDate(time.Now())

func seedBidMeta(t *testing.T, mr *miniredis.Miniredis, bidID string, priceCPM float64, isTest int) {
	t.Helper()
	meta, _ := json.Marshal(map[string]any{
		"campaign_id": "camp-L", "line_item_id": "li-L", "creative_id": "cr-L",
		"supply_partner_id": "sp-L", "price": priceCPM, "is_test": isTest,
	})
	mr.Set("bidmeta:"+bidID, string(meta))
}

func lossRequest(t *testing.T, bidID string) *http.Request {
	t.Helper()
	tok, err := SignEventToken(bidID, time.Hour)
	if err != nil {
		t.Fatalf("sign: %v", err)
	}
	return httptest.NewRequest(http.MethodPost, "/rtb/loss?bid="+tok+"&reason=102&mtw=1.5", nil)
}

// streamEvents читает rtb:events как список map[поле]значение
// (miniredis отдаёт Values плоским срезом k,v,k,v).
func streamEvents(t *testing.T, mr *miniredis.Miniredis) []map[string]string {
	t.Helper()
	entries, err := mr.Stream("rtb:events")
	if err != nil {
		return nil
	}
	out := make([]map[string]string, 0, len(entries))
	for _, e := range entries {
		m := make(map[string]string, len(e.Values)/2)
		for i := 0; i+1 < len(e.Values); i += 2 {
			m[e.Values[i]] = e.Values[i+1]
		}
		out = append(out, m)
	}
	return out
}

func spendOf(t *testing.T, mr *miniredis.Miniredis, key string) float64 {
	t.Helper()
	raw, err := mr.Get(key)
	if err != nil {
		return 0
	}
	f, _ := strconv.ParseFloat(raw, 64)
	return f
}

// Резерв 0.01 руб (CPM 10) → 50 параллельных повторов lurl → возвращено
// ровно 0.01, одно событие loss в стриме.
func TestLoss_ConcurrentRepeatsReleaseOnce(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	ctx := context.Background()
	const priceCPM = 10.0
	cost := priceCPM / 1000.0

	if ok, err := h.redis.GateAndReserve(ctx, "camp-L", "li-L", cost, 100, 0, "", nil); err != nil || !ok {
		t.Fatalf("reserve: ok=%v err=%v", ok, err)
	}
	seedBidMeta(t, mr, "bid-1", priceCPM, 0)

	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			rec := httptest.NewRecorder()
			h.Loss(rec, lossRequest(t, "bid-1"))
			if rec.Code != http.StatusNoContent {
				t.Errorf("status %d", rec.Code)
			}
		}()
	}
	wg.Wait()

	if got := spendOf(t, mr, redisx.SpendKey("camp", "camp-L", today)); got > 1e-9 || got < -1e-9 {
		t.Fatalf("spend:camp after single release must be 0, got %v (double release = negative)", got)
	}
	if got := spendOf(t, mr, redisx.SpendKey("li", "li-L", today)); got > 1e-9 || got < -1e-9 {
		t.Fatalf("spend:li after single release must be 0, got %v", got)
	}
	if n := len(streamEvents(t, mr)); n != 1 {
		t.Fatalf("rtb:events len = %d, want exactly 1 loss event", n)
	}
	if st, _ := mr.Get(redisx.BidStateKey("bid-1")); st != "loss" {
		t.Fatalf("terminal state bidstate:{bid} must be 'loss', got %q", st)
	}
}

// test=1: SSP проверяет интеграцию — резерв не трогаем (его и не было),
// но событие пишем с is_test=1, чтобы отчёт видел тест-трафик отдельно.
func TestLoss_TestBidDoesNotTouchSpend(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	mr.Set(redisx.SpendKey("camp", "camp-L", today), "5")
	mr.Set(redisx.SpendKey("li", "li-L", today), "5")
	seedBidMeta(t, mr, "bid-test", 10, 1)

	rec := httptest.NewRecorder()
	h.Loss(rec, lossRequest(t, "bid-test"))
	if rec.Code != http.StatusNoContent {
		t.Fatalf("status %d", rec.Code)
	}
	if got := spendOf(t, mr, redisx.SpendKey("camp", "camp-L", today)); got != 5 {
		t.Fatalf("test bid must not release: spend:camp=%v", got)
	}
	msgs := streamEvents(t, mr)
	if len(msgs) != 1 {
		t.Fatalf("want 1 event, got %d", len(msgs))
	}
	if msgs[0]["is_test"] != "1" || msgs[0]["type"] != "loss" {
		t.Fatalf("event must be loss/is_test=1, got %v", msgs[0])
	}
}

// Неизвестный bid (bidmeta истёк или его не было): 204, ничего не меняем,
// событие не пишем — иначе SSP мог бы «возвращать» чужой бюджет.
func TestLoss_UnknownBidIsNoop(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	mr.Set(redisx.SpendKey("camp", "camp-L", today), "5")

	rec := httptest.NewRecorder()
	h.Loss(rec, lossRequest(t, "ghost"))
	if rec.Code != http.StatusNoContent {
		t.Fatalf("status %d", rec.Code)
	}
	if got := spendOf(t, mr, redisx.SpendKey("camp", "camp-L", today)); got != 5 {
		t.Fatalf("unknown bid must not release: %v", got)
	}
	if n := len(streamEvents(t, mr)); n != 0 {
		t.Fatalf("unknown bid must not emit events, got %d", n)
	}
}

// Битый/чужой токен — 403 и ноль побочных эффектов.
func TestLoss_InvalidTokenForbidden(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	mr.Set(redisx.SpendKey("camp", "camp-L", today), "5")
	rec := httptest.NewRecorder()
	h.Loss(rec, httptest.NewRequest(http.MethodPost, "/rtb/loss?bid=v1.forged.token", nil))
	if rec.Code != http.StatusForbidden {
		t.Fatalf("status %d, want 403", rec.Code)
	}
	if got := spendOf(t, mr, redisx.SpendKey("camp", "camp-L", today)); got != 5 {
		t.Fatalf("forged token must not release: %v", got)
	}
}

// Макросы, не подставленные SSP, не должны утекать в событие как строки "${...}".
func TestLoss_UnsubstitutedMacrosBecomeEmpty(t *testing.T) {
	h, mr := newMiniRedisHandler(t)
	seedBidMeta(t, mr, "bid-m", 10, 0)
	tok, _ := SignEventToken("bid-m", time.Hour)
	req := httptest.NewRequest(http.MethodPost,
		fmt.Sprintf("/rtb/loss?bid=%s&reason=${AUCTION_LOSS}&mtw=${AUCTION_MIN_TO_WIN}", tok), nil)
	rec := httptest.NewRecorder()
	h.Loss(rec, req)
	msgs := streamEvents(t, mr)
	if len(msgs) != 1 {
		t.Fatalf("want 1 event, got %d", len(msgs))
	}
	if msgs[0]["loss_reason"] != "" || msgs[0]["min_to_win"] != "" {
		t.Fatalf("raw macros leaked into event: %v", msgs[0])
	}
}
