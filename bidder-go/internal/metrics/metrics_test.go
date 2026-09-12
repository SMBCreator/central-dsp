package metrics

import (
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

func scrape(t *testing.T, r *Registry) string {
	t.Helper()
	rec := httptest.NewRecorder()
	r.Handler().ServeHTTP(rec, httptest.NewRequest("GET", "/metrics", nil))
	if rec.Code != 200 {
		t.Fatalf("scrape status = %d", rec.Code)
	}
	ct := rec.Header().Get("Content-Type")
	if !strings.Contains(ct, "version=0.0.4") {
		t.Fatalf("wrong content type: %s", ct)
	}
	return rec.Body.String()
}

func TestCountersAndReasons(t *testing.T) {
	r := New()
	r.IncBidRequest()
	r.IncBidRequest()
	r.IncBidResponse(3, 150.5)
	r.IncNoBid(ReasonQPSExceeded)
	r.IncNoBid(ReasonQPSExceeded)
	r.IncNoBid(ReasonParseError)
	r.IncGateReject()
	r.IncGateError()
	r.IncLossRelease()

	body := scrape(t, r)
	for _, want := range []string{
		"bidder_bid_requests_total 2",
		"bidder_bid_responses_total 1",
		"bidder_bids_total 3",
		`bidder_no_bid_total{reason="qps_exceeded"} 2`,
		`bidder_no_bid_total{reason="parse_error"} 1`,
		`bidder_no_bid_total{reason="feed_not_loaded"} 0`, // нулевые серии видны сразу
		"bidder_gate_rejects_total 1",
		"bidder_gate_errors_total 1",
		"bidder_loss_releases_total 1",
		"bidder_bid_price_cpm_rub_sum 150.5",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("missing %q in scrape:\n%s", want, body)
		}
	}
}

func TestLatencyHistogram(t *testing.T) {
	r := New()
	r.ObserveLatency(0.004) // bucket le=0.005
	r.ObserveLatency(0.09)  // bucket le=0.1
	r.ObserveLatency(3)     // +Inf

	body := scrape(t, r)
	for _, want := range []string{
		`bidder_bid_latency_seconds_bucket{le="0.005"} 1`,
		`bidder_bid_latency_seconds_bucket{le="0.1"} 2`,
		`bidder_bid_latency_seconds_bucket{le="+Inf"} 3`,
		"bidder_bid_latency_seconds_count 3",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("missing %q in scrape:\n%s", want, body)
		}
	}
	// Кумулятивность: le=0.25 должен включать все <=0.25 (2 наблюдения).
	if !strings.Contains(body, `bidder_bid_latency_seconds_bucket{le="0.25"} 2`) {
		t.Errorf("histogram buckets are not cumulative:\n%s", body)
	}
}

func TestGauges(t *testing.T) {
	r := New()
	r.RegisterGauge("bidder_feed_age_seconds", func() float64 { return 42 })
	body := scrape(t, r)
	if !strings.Contains(body, "bidder_feed_age_seconds 42") {
		t.Errorf("gauge missing:\n%s", body)
	}
}

func TestUnknownReasonIgnored(t *testing.T) {
	r := New()
	r.IncNoBid(NoBidReason("out_of_taxonomy")) // не паникует и не создаёт серию
	body := scrape(t, r)
	if strings.Contains(body, "out_of_taxonomy") {
		t.Errorf("unbounded label leaked into scrape:\n%s", body)
	}
}

func TestConcurrentIncrements(t *testing.T) {
	r := New()
	var wg sync.WaitGroup
	for range 50 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range 100 {
				r.IncBidRequest()
				r.IncNoBid(ReasonNoCandidates)
				r.ObserveLatency(0.01)
			}
		}()
	}
	wg.Wait()
	body := scrape(t, r)
	if !strings.Contains(body, "bidder_bid_requests_total 5000") {
		t.Errorf("lost increments under concurrency:\n%s", body)
	}
	if !strings.Contains(body, `bidder_no_bid_total{reason="no_candidates"} 5000`) {
		t.Errorf("lost no-bid increments under concurrency:\n%s", body)
	}
	if !strings.Contains(body, "bidder_bid_latency_seconds_count 5000") {
		t.Errorf("lost latency observations under concurrency:\n%s", body)
	}
}
