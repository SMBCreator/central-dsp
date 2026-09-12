package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
)

func TestRecoverMiddleware_PanicBecomesNoBidAndCounted(t *testing.T) {
	reg := metrics.New()
	boom := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		var m map[string]int
		m["x"] = 1 // nil map write -> panic
	})
	h := recoverMiddleware(boom, reg)

	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/rtb/bid/k", nil))
	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204 (no-bid)", rec.Code)
	}

	mrec := httptest.NewRecorder()
	reg.Handler().ServeHTTP(mrec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if !strings.Contains(mrec.Body.String(), "bidder_panics_total 1\n") {
		t.Fatalf("panic counter not exported:\n%s", mrec.Body.String())
	}
}

func TestRecoverMiddleware_PassThrough(t *testing.T) {
	reg := metrics.New()
	ok := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	rec := httptest.NewRecorder()
	recoverMiddleware(ok, reg).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
}

func TestRecoverMiddleware_ErrAbortHandlerIsRethrown(t *testing.T) {
	reg := metrics.New()
	abort := http.HandlerFunc(func(http.ResponseWriter, *http.Request) { panic(http.ErrAbortHandler) })
	defer func() {
		if r := recover(); r != http.ErrAbortHandler {
			t.Fatalf("expected ErrAbortHandler to propagate, got %v", r)
		}
	}()
	recoverMiddleware(abort, reg).ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/", nil))
}

// Audit O-05: Docker HEALTHCHECK опрашивает только /livez. Даже если readiness
// (/healthz) отдаёт 503 из-за фида или Redis, контейнер остаётся healthy.
func TestHealthcheckUsesLivezNotHealthz(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /livez", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusServiceUnavailable) })
	srv := httptest.NewServer(mux)
	defer srv.Close()
	addr := strings.TrimPrefix(srv.URL, "http://")
	if rc := healthcheck(addr); rc != 0 {
		t.Fatalf("healthcheck must pass on /livez 200 while /healthz is 503, got exit %d", rc)
	}
}

func TestHealthcheckFailsWhenProcessDoesNotAnswer(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /livez", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusServiceUnavailable) })
	srv := httptest.NewServer(mux)
	addr := strings.TrimPrefix(srv.URL, "http://")
	if rc := healthcheck(addr); rc != 1 {
		t.Fatalf("livez 503: want exit 1, got %d", rc)
	}
	srv.Close()
	if rc := healthcheck(addr); rc != 1 {
		t.Fatalf("server gone: want exit 1, got %d", rc)
	}
}
