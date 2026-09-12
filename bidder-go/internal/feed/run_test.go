package feed

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

// Backend, который первые failFirst запросов отвечает 503, потом — пустым
// валидным фидом. Имитирует backend, ещё гоняющий миграции при старте стека.
func flakyBackend(t *testing.T, failFirst int32) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) <= failFirst {
			http.Error(w, "starting", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/rtb-feed/campaigns":
			_, _ = w.Write([]byte(`{"candidates":[],"domain_lists":[],"optimizer_blocks":[],"scoring_blocks":[],"dirty_domains":[]}`))
		case "/rtb-feed/partners":
			_, _ = w.Write([]byte(`{"partners":[],"fx_rates":{}}`))
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &calls
}

// Run обязан догрузить фид быстрыми повторами, а не через полный Refresh:
// Refresh=1h, backend отдаёт 503 три раза, снапшот должен появиться за секунды.
func TestRunRetriesWithBackoffUntilFirstLoad(t *testing.T) {
	srv, calls := flakyBackend(t, 3)
	s := NewStore(Config{BackendURL: srv.URL, FeedToken: "t", Refresh: time.Hour, MaxAge: time.Minute})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go s.Run(ctx)

	deadline := time.Now().Add(15 * time.Second)
	for !s.Fresh() {
		if time.Now().After(deadline) {
			t.Fatalf("feed not loaded within deadline; backend calls=%d, refresh errors=%d, status=%+v",
				calls.Load(), s.RefreshErrors(), s.Status())
		}
		time.Sleep(20 * time.Millisecond)
	}
	// 3 отказа по /campaigns + 1 успешный /campaigns + 1 /partners = 5.
	if got := calls.Load(); got != 5 {
		t.Fatalf("backend calls = %d, want 5 (3 failures, then campaigns+partners)", got)
	}
	if s.RefreshErrors() != 3 {
		t.Fatalf("refresh errors = %d, want 3", s.RefreshErrors())
	}
}

// Отмена ctx во время startup-backoff завершает Run, а не оставляет горутину.
func TestRunStopsDuringStartupBackoff(t *testing.T) {
	srv, _ := flakyBackend(t, 1<<30) // backend никогда не поднимается
	s := NewStore(Config{BackendURL: srv.URL, FeedToken: "t", Refresh: time.Hour, MaxAge: time.Minute})

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { s.Run(ctx); close(done) }()
	time.Sleep(50 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("Run did not return after ctx cancel during startup backoff")
	}
	if s.Fresh() {
		t.Fatal("feed must not be Fresh without a single successful load")
	}
}
