package rtb

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

func newTestHandler() *Handler {
	return NewHandler(feed.NewStore(feed.Config{BackendURL: "http://localhost:0"}))
}

func TestBidInvalidJSON(t *testing.T) {
	h := newTestHandler()
	r := httptest.NewRequest(http.MethodPost, "/rtb/bid/key1", strings.NewReader("{not json"))
	r.SetPathValue("endpointKey", "key1")
	w := httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("want 400, got %d", w.Code)
	}
}

func TestBidNoImpsIsNoBid(t *testing.T) {
	h := newTestHandler()
	r := httptest.NewRequest(http.MethodPost, "/rtb/bid/key1", strings.NewReader(`{"id":"r1","imp":[]}`))
	r.SetPathValue("endpointKey", "key1")
	w := httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusNoContent {
		t.Fatalf("want 204 no-bid, got %d", w.Code)
	}
}

func TestBidSkeletonNoBids(t *testing.T) {
	h := newTestHandler()
	body := `{"id":"r2","at":1,"imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":10.5}]}`
	r := httptest.NewRequest(http.MethodPost, "/rtb/bid/key1", strings.NewReader(body))
	r.SetPathValue("endpointKey", "key1")
	w := httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusNoContent {
		t.Fatalf("want 204, got %d", w.Code)
	}
}

func TestHealthz(t *testing.T) {
	h := newTestHandler()
	r := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	h.Healthz(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), `"status":"ok"`) {
		t.Fatalf("healthz body: %s", w.Body.String())
	}
}
