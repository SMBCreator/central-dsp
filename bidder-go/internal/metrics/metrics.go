// Package metrics — Prometheus-метрики hot path без внешних зависимостей.
//
// Экспозиция в text format 0.0.4 (полностью совместим со скрейпом Prometheus).
// Скелет биддера сознательно stdlib-only (см. go.mod); при переходе на
// github.com/prometheus/client_golang интерфейс сохраняется, меняется backend.
//
// Правила (см. skill: bidder observability):
//   - только bounded-лейблы: фиксированные таксономии причин/статусов;
//     никаких auction id / URL / device id в лейблах;
//   - инкременты lock-free (atomic), безопасны в hot path;
//   - гистограмма латентности с фикс. бакетами под RTB-бюджет (<=100ms).
package metrics

import (
	"fmt"
	"math"
	"net/http"
	"sort"
	"sync"
	"sync/atomic"
)

// NoBidReason — фиксированная таксономия причин no-bid (bounded label).
type NoBidReason string

const (
	ReasonFeedNotLoaded   NoBidReason = "feed_not_loaded"
	ReasonFeedStale       NoBidReason = "feed_stale" // audit R-02: снапшот старше FEED_MAX_AGE
	ReasonUnknownEndpoint NoBidReason = "unknown_endpoint"
	ReasonBadAuth         NoBidReason = "bad_auth" // токен не совпал
	// Audit S-06: раздельные причины отказа на auth-фазе, чтобы алерт
	// «SSP шлёт неверный токен» не смешивался с «токен отозван/не выдан»
	// и «мы режем по IP».
	ReasonTokenNotIssued   NoBidReason = "token_not_issued"  // auth_token_hash пуст: не выдан или отозван
	ReasonIPDenied         NoBidReason = "ip_denied"         // клиентский IP вне ip_whitelist
	ReasonWhitelistInvalid NoBidReason = "whitelist_invalid" // ip_whitelist не разобрался -> fail-closed
	ReasonQPSExceeded      NoBidReason = "qps_exceeded"
	ReasonParseError       NoBidReason = "parse_error"
	ReasonEmptyRequest     NoBidReason = "empty_request"
	ReasonCurrency         NoBidReason = "currency_mismatch"
	ReasonNoCandidates     NoBidReason = "no_candidates"
)

// AllNoBidReasons — полный список для инициализации серий (нулевые значения
// видны в Prometheus сразу, rate() не «прыгает» при первом инкременте).
var AllNoBidReasons = []NoBidReason{
	ReasonFeedNotLoaded, ReasonFeedStale, ReasonUnknownEndpoint, ReasonBadAuth,
	ReasonTokenNotIssued, ReasonIPDenied, ReasonWhitelistInvalid, ReasonQPSExceeded,
	ReasonParseError, ReasonEmptyRequest, ReasonCurrency, ReasonNoCandidates,
}

// latencyBuckets — секунды; RTB-бюджет ответа <=100ms, поэтому сетка плотная снизу.
var latencyBuckets = []float64{0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 1}

// Registry — все метрики биддера. Потокобезопасен.
type Registry struct {
	bidRequests   atomic.Int64 // всего входящих bid-запросов
	bidResponses  atomic.Int64 // ответов с >=1 бидом
	bidsSubmitted atomic.Int64 // всего бидов (по impression)

	noBid sync.Map // NoBidReason -> *atomic.Int64

	// Audit R-03: предфильтр (bot_ua / geo_unserved / no_device_id /
	// datacenter_ip) — отдельно от no-bid, как rtb_prefilter_drops_total в Python.
	prefilterDrops sync.Map // reason -> *atomic.Int64

	gateRejects     atomic.Int64 // gate_and_reserve: отказ (бюджет/freq cap)
	gateErrors      atomic.Int64 // gate_and_reserve: ошибка Redis
	lossReleases    atomic.Int64 // /rtb/loss: освобождение резерва
	reserveExpired  atomic.Int64 // sweeper: резерв истёк без win/loss
	reserveOrphans  atomic.Int64 // sweeper: pending без bidmeta
	eventEmitErrors atomic.Int64 // XADD в rtb:events не удался (событие потеряно для статистики)
	// Audit R-02: bidmeta/XADD не записались -> бид отменён, резерв возвращён.
	// Каждая единица — упущенный показ из-за Redis, а не потерянные деньги.
	bookkeepingErrors atomic.Int64
	panics            atomic.Int64 // паники хендлеров, перехваченные recover-middleware

	// Per-partner (endpoint_key): запросы и биды. Кардинальность = число SSP
	// (единицы-десятки), поэтому безопасно для Prometheus.
	partnerRequests sync.Map // endpoint_key -> *atomic.Int64
	partnerBids     sync.Map // endpoint_key -> *atomic.Int64

	// Суммы цен (RUB CPM) для avg bid price = sum/count. Fixed-point milli-RUB,
	// т.к. atomic float в stdlib нет.
	bidPriceMilliCPM atomic.Int64

	latency histogram

	// Gauges снимаются колбэками в момент скрейпа.
	gaugesMu sync.Mutex
	gauges   map[string]func() float64
}

type histogram struct {
	counts [12]atomic.Int64 // len(latencyBuckets)+1 (последний = +Inf)
	sumUS  atomic.Int64     // сумма в микросекундах
	total  atomic.Int64
}

// New создаёт реестр с преаллоцированными сериями no-bid.
func New() *Registry {
	r := &Registry{gauges: map[string]func() float64{}}
	for _, reason := range AllNoBidReasons {
		r.noBid.Store(reason, new(atomic.Int64))
	}
	for _, reason := range PrefilterReasons {
		r.prefilterDrops.Store(reason, new(atomic.Int64))
	}
	return r
}

// PrefilterReasons — bounded enum причин предфильтра (см. internal/prefilter).
// Дублируется строками, чтобы metrics не зависел от prefilter.
var PrefilterReasons = []string{"bot_ua", "geo_unserved", "no_device_id", "datacenter_ip"}

// IncPrefilterDrop — запрос отсечён предфильтром до таргетинга/аукциона.
func (r *Registry) IncPrefilterDrop(reason string) {
	if v, ok := r.prefilterDrops.Load(reason); ok {
		v.(*atomic.Int64).Add(1)
	}
}

// IncBidRequest — входящий bid-запрос.
func (r *Registry) IncBidRequest() { r.bidRequests.Add(1) }

// IncBidResponse — ответ с >=1 бидом; n — число бидов, priceCPM — сумма их цен (RUB CPM).
func (r *Registry) IncBidResponse(n int, priceCPM float64) {
	r.bidResponses.Add(1)
	r.bidsSubmitted.Add(int64(n))
	r.bidPriceMilliCPM.Add(int64(priceCPM * 1000))
}

// IncNoBid — no-bid с причиной из фиксированной таксономии.
func (r *Registry) IncNoBid(reason NoBidReason) {
	if v, ok := r.noBid.Load(reason); ok {
		v.(*atomic.Int64).Add(1)
	}
}

// IncGateReject / IncGateError / IncLossRelease — бюджет/freq-гейт.
func (r *Registry) IncGateReject()  { r.gateRejects.Add(1) }
func (r *Registry) IncGateError()   { r.gateErrors.Add(1) }
func (r *Registry) IncLossRelease() { r.lossReleases.Add(1) }

// IncReserveExpired / IncReserveOrphans — sweeper: резервы без win/loss,
// возвращённые по таймауту; orphans — pending без bidmeta (деньги вернёт reconcile).
func (r *Registry) IncReserveExpired(n int) { r.reserveExpired.Add(int64(n)) }
func (r *Registry) IncReserveOrphans(n int) { r.reserveOrphans.Add(int64(n)) }

// IncEventEmitError — не удалось положить событие в Redis Stream.
func (r *Registry) IncEventEmitError() { r.eventEmitErrors.Add(1) }

// IncBookkeepingError — бид отменён: bidmeta или событие bid не записались (R-02).
func (r *Registry) IncBookkeepingError() { r.bookkeepingErrors.Add(1) }

// IncPanic — хендлер упал с паникой, процесс выжил благодаря recover.
func (r *Registry) IncPanic() { r.panics.Add(1) }

// IncPartnerRequest / IncPartnerBids — счётчики по SSP (endpoint_key).
func (r *Registry) IncPartnerRequest(endpointKey string) {
	incMap(&r.partnerRequests, endpointKey, 1)
}
func (r *Registry) IncPartnerBids(endpointKey string, n int) {
	incMap(&r.partnerBids, endpointKey, int64(n))
}

func incMap(m *sync.Map, key string, n int64) {
	if key == "" {
		key = "unknown"
	}
	v, ok := m.Load(key)
	if !ok {
		v, _ = m.LoadOrStore(key, new(atomic.Int64))
	}
	v.(*atomic.Int64).Add(n)
}

func writeLabeled(w http.ResponseWriter, m *sync.Map, name, help string) {
	fmt.Fprintf(w, "# HELP %s %s\n# TYPE %s counter\n", name, help, name)
	keys := make([]string, 0, 8)
	m.Range(func(k, _ any) bool { keys = append(keys, k.(string)); return true })
	sort.Strings(keys)
	for _, k := range keys {
		v, _ := m.Load(k)
		fmt.Fprintf(w, "%s{endpoint_key=%q} %d\n", name, k, v.(*atomic.Int64).Load())
	}
}

// ObserveLatency — латентность обработки bid-запроса в секундах.
func (r *Registry) ObserveLatency(sec float64) {
	idx := len(latencyBuckets)
	for i, b := range latencyBuckets {
		if sec <= b {
			idx = i
			break
		}
	}
	r.latency.counts[idx].Add(1)
	r.latency.sumUS.Add(int64(sec * 1e6))
	r.latency.total.Add(1)
}

// RegisterGauge — gauge, значение которого читается при каждом скрейпе
// (feed age, campaigns loaded, redis up).
func (r *Registry) RegisterGauge(name string, fn func() float64) {
	r.gaugesMu.Lock()
	defer r.gaugesMu.Unlock()
	r.gauges[name] = fn
}

// Handler — GET /metrics в Prometheus text format 0.0.4.
func (r *Registry) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		r.write(w)
	})
}

func (r *Registry) write(w http.ResponseWriter) {
	c := func(name, help string, v int64) {
		fmt.Fprintf(w, "# HELP %s %s\n# TYPE %s counter\n%s %d\n", name, help, name, name, v)
	}
	c("bidder_bid_requests_total", "Incoming OpenRTB bid requests.", r.bidRequests.Load())
	c("bidder_bid_responses_total", "Responses containing at least one bid.", r.bidResponses.Load())
	c("bidder_bids_total", "Individual bids submitted (per impression).", r.bidsSubmitted.Load())

	fmt.Fprintf(w, "# HELP bidder_no_bid_total No-bid outcomes by reason.\n# TYPE bidder_no_bid_total counter\n")
	reasons := make([]string, 0, len(AllNoBidReasons))
	for _, reason := range AllNoBidReasons {
		reasons = append(reasons, string(reason))
	}
	sort.Strings(reasons)
	for _, reason := range reasons {
		v, _ := r.noBid.Load(NoBidReason(reason))
		fmt.Fprintf(w, "bidder_no_bid_total{reason=%q} %d\n", reason, v.(*atomic.Int64).Load())
	}

	fmt.Fprintf(w, "# HELP bidder_prefilter_drops_total Requests dropped by the O(1) prefilter before targeting (not counted as no-bid).\n# TYPE bidder_prefilter_drops_total counter\n")
	for _, reason := range PrefilterReasons {
		v, _ := r.prefilterDrops.Load(reason)
		fmt.Fprintf(w, "bidder_prefilter_drops_total{reason=%q} %d\n", reason, v.(*atomic.Int64).Load())
	}

	c("bidder_gate_rejects_total", "gate_and_reserve rejections (budget or frequency cap).", r.gateRejects.Load())
	c("bidder_gate_errors_total", "gate_and_reserve Redis errors.", r.gateErrors.Load())
	c("bidder_loss_releases_total", "Budget reservations released via /rtb/loss.", r.lossReleases.Load())
	c("bidder_reserve_expired_total", "Reservations expired by the sweeper (no win/loss in time).", r.reserveExpired.Load())
	c("bidder_reserve_orphans_total", "pending:bids entries whose bidmeta was already gone.", r.reserveOrphans.Load())
	c("bidder_event_emit_errors_total", "Bid events that failed to be written to the rtb:events Redis stream.", r.eventEmitErrors.Load())
	c("bidder_bookkeeping_errors_total", "Bids withdrawn because bidmeta/bid event could not be written to Redis (reservation released).", r.bookkeepingErrors.Load())
	c("bidder_panics_total", "Handler panics recovered by the HTTP middleware (process kept alive).", r.panics.Load())

	writeLabeled(w, &r.partnerRequests, "bidder_partner_requests_total", "Bid requests by SSP endpoint_key.")
	writeLabeled(w, &r.partnerBids, "bidder_partner_bids_total", "Bids submitted by SSP endpoint_key.")

	fmt.Fprintf(w, "# HELP bidder_bid_price_cpm_rub_sum Sum of submitted bid prices (RUB CPM). Avg = sum/bidder_bids_total.\n# TYPE bidder_bid_price_cpm_rub_sum counter\n")
	fmt.Fprintf(w, "bidder_bid_price_cpm_rub_sum %g\n", float64(r.bidPriceMilliCPM.Load())/1000)

	// Гистограмма латентности.
	fmt.Fprintf(w, "# HELP bidder_bid_latency_seconds Bid request handling latency.\n# TYPE bidder_bid_latency_seconds histogram\n")
	var cum int64
	for i, b := range latencyBuckets {
		cum += r.latency.counts[i].Load()
		fmt.Fprintf(w, "bidder_bid_latency_seconds_bucket{le=%q} %d\n", formatFloat(b), cum)
	}
	cum += r.latency.counts[len(latencyBuckets)].Load()
	fmt.Fprintf(w, "bidder_bid_latency_seconds_bucket{le=\"+Inf\"} %d\n", cum)
	fmt.Fprintf(w, "bidder_bid_latency_seconds_sum %g\n", float64(r.latency.sumUS.Load())/1e6)
	fmt.Fprintf(w, "bidder_bid_latency_seconds_count %d\n", r.latency.total.Load())

	// Gauges.
	r.gaugesMu.Lock()
	names := make([]string, 0, len(r.gauges))
	for name := range r.gauges {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		v := r.gauges[name]()
		if math.IsNaN(v) || math.IsInf(v, 0) {
			v = 0
		}
		fmt.Fprintf(w, "# TYPE %s gauge\n%s %g\n", name, name, v)
	}
	r.gaugesMu.Unlock()
}

func formatFloat(f float64) string {
	return fmt.Sprintf("%g", f)
}
