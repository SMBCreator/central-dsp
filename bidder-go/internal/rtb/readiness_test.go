package rtb

// Audit O-05: liveness и readiness разделены. /livez — только процесс,
// /healthz — фид + Redis; при Redis down биддер не готов и не ставит бидов
// без резерва (деньги не трогаются).

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/redisx"
	"github.com/alicebob/miniredis/v2"
)

func newReadyHandler(t *testing.T) (*Handler, *miniredis.Miniredis, *feed.Store) {
	t.Helper()
	mr := miniredis.RunT(t)
	rc, err := redisx.New("redis://" + mr.Addr() + "/0")
	if err != nil {
		t.Fatalf("redisx.New: %v", err)
	}
	t.Cleanup(func() { _ = rc.Close() })
	store := feed.NewStore(feed.Config{BackendURL: "http://localhost:0", MaxAge: time.Minute})
	store.SetSnapshot(testSnapshot())
	return NewHandler(store, rc), mr, store
}

func getJSON(t *testing.T, fn http.HandlerFunc, path string) (int, map[string]any, string) {
	t.Helper()
	w := httptest.NewRecorder()
	fn(w, httptest.NewRequest(http.MethodGet, path, nil))
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("%s: invalid json %q: %v", path, w.Body.String(), err)
	}
	return w.Code, body, w.Body.String()
}

func reasonsOf(body map[string]any) []string {
	raw, _ := body["reasons"].([]any)
	out := make([]string, 0, len(raw))
	for _, r := range raw {
		out = append(out, r.(string))
	}
	return out
}

func TestReadinessRedisUpAndFreshFeed200(t *testing.T) {
	h, _, _ := newReadyHandler(t)
	code, body, _ := getJSON(t, h.Healthz, "/healthz")
	if code != http.StatusOK {
		t.Fatalf("healthz: want 200, got %d (%v)", code, body)
	}
	if body["status"] != "ok" || body["ready"] != true || body["redis"] != true || body["redis_configured"] != true {
		t.Fatalf("healthz body: %v", body)
	}
	if len(reasonsOf(body)) != 0 {
		t.Fatalf("reasons must be empty when ready: %v", body["reasons"])
	}
}

func TestReadinessRedisDown503AndLivezStays200(t *testing.T) {
	h, mr, _ := newReadyHandler(t)
	addr := mr.Addr()
	mr.Close()

	code, body, raw := getJSON(t, h.Healthz, "/healthz")
	if code != http.StatusServiceUnavailable {
		t.Fatalf("healthz with redis down: want 503, got %d", code)
	}
	if body["status"] != NotReadyRedisDown || body["ready"] != false || body["redis"] != false {
		t.Fatalf("healthz body: %v", body)
	}
	if rs := reasonsOf(body); len(rs) != 1 || rs[0] != NotReadyRedisDown {
		t.Fatalf("reasons: want [redis_down], got %v", rs)
	}
	// Никаких адресов/ошибок соединения наружу: только безопасные причины.
	for _, leak := range []string{addr, "dial", "refused", "feed_error", "127.0.0.1"} {
		if strings.Contains(raw, leak) {
			t.Fatalf("healthz leaks %q: %s", leak, raw)
		}
	}

	code, body, _ = getJSON(t, h.Livez, "/livez")
	if code != http.StatusOK || body["status"] != "alive" {
		t.Fatalf("livez must stay 200 with redis down, got %d %v", code, body)
	}
}

func TestReadinessRedisDownNoBidWithoutReservation(t *testing.T) {
	// Redis лежит -> GateAndReserve не может забронировать бюджет -> кандидат
	// пропущен, ответ без бида. Ставок «в долг» без резерва быть не должно.
	h, mr, _ := newReadyHandler(t)
	mr.Close()
	w := doBid(t, h, `{"id":"r1","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`)
	if w.Code != http.StatusNoContent {
		t.Fatalf("bid with redis down: want 204 (no bid), got %d body=%s", w.Code, w.Body.String())
	}
}

func TestReadinessFeedBeforeRedisInReasons(t *testing.T) {
	// Фид не загружен и Redis лежит: status = feed_not_loaded (приоритет), обе
	// причины перечислены.
	mr := miniredis.RunT(t)
	rc, err := redisx.New("redis://" + mr.Addr() + "/0")
	if err != nil {
		t.Fatalf("redisx.New: %v", err)
	}
	t.Cleanup(func() { _ = rc.Close() })
	store := feed.NewStore(feed.Config{BackendURL: "http://localhost:0", MaxAge: time.Minute})
	h := NewHandler(store, rc)
	mr.Close()

	rd := h.Readiness(context.Background())
	if rd.Ready || rd.Status() != NotReadyFeedNotLoaded {
		t.Fatalf("readiness: %+v", rd)
	}
	if len(rd.Reasons) != 2 || rd.Reasons[1] != NotReadyRedisDown {
		t.Fatalf("reasons: want [feed_not_loaded redis_down], got %v", rd.Reasons)
	}
}

func TestReadinessWithoutRedisConfigured(t *testing.T) {
	// dev без REDIS_URL: readiness зависит только от фида, redis_configured=false.
	h := newTestHandler(testSnapshot())
	code, body, _ := getJSON(t, h.Healthz, "/healthz")
	if code != http.StatusOK || body["redis_configured"] != false || body["redis"] != false {
		t.Fatalf("healthz without redis: %d %v", code, body)
	}
}
