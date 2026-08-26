package rtb

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// Handler обслуживает RTB hot path. Контракт эндпоинтов идентичен
// Python-reference (backend/routers/rtb.py) — см. parity_matrix.md.
type Handler struct {
	store *feed.Store
}

func NewHandler(store *feed.Store) *Handler {
	return &Handler{store: store}
}

// Bid — POST /rtb/bid/{endpointKey}.
// Пайплайн (спека go_bidder_plan.md §hot-path, паритет с Python):
//  1. auth: endpointKey -> supply partner (+ опциональный X-SSP-Token)
//  2. QPS-гейт партнёра (Redis INCR+EXPIRE)
//  3. parse/validate BidRequest, auction type (at, default 2)
//  4. targeting: geo/os/devicetype/domain lists (global+campaign)/SSP allowlist
//  5. audiences: include/exclude по Redis sets (device ifa + user.eids)
//  6. freq caps: multi-window (до 3 окон), атомарный Lua-скрипт
//  7. budget: atomic reserve (Lua из backend/cache.py — переносится 1:1)
//  8. price: Decimal-семантика, bid shading только при at=1, floor*1.01 нижняя граница
//  9. response: adm с erid-маркировкой (banner/native/VAST), cur=RUB, nurl/lurl
func (h *Handler) Bid(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	endpointKey := r.PathValue("endpointKey")

	var req BidRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid bid request", http.StatusBadRequest)
		return
	}
	if req.ID == "" || len(req.Imp) == 0 {
		w.WriteHeader(http.StatusNoContent) // no-bid
		return
	}

	// TODO(impl): полный пайплайн по спеке. Скелет отвечает no-bid.
	_ = endpointKey
	_ = h.store

	slog.Debug("bid request processed", "id", req.ID, "took", time.Since(start))
	w.WriteHeader(http.StatusNoContent)
}

// Win — POST /rtb/win: биллинг выигрыша (идемпотентный, mark_once-семантика),
// clearing price clamp, bump freq caps, обучающий сигнал для shading.
func (h *Handler) Win(w http.ResponseWriter, r *http.Request) {
	// TODO(impl): перенести bill_win-семантику (backend/billing_core.py):
	// Decimal, идемпотентность по bid id, clamp цены [floor, bid_price].
	w.WriteHeader(http.StatusNoContent)
}

// Loss — POST /rtb/loss: освобождение резерва бюджета + loss-сигнал для shading.
func (h *Handler) Loss(w http.ResponseWriter, r *http.Request) {
	// TODO(impl): release budget reserve; записать loss в shading-агрегаты.
	w.WriteHeader(http.StatusNoContent)
}

// Imp — POST /rtb/imp: импрешен-нотификация (маркировка показа для ОРД-отчётности).
func (h *Handler) Imp(w http.ResponseWriter, r *http.Request) {
	// TODO(impl): учёт показа, ОРД-статистика.
	w.WriteHeader(http.StatusNoContent)
}

// Healthz — GET /healthz: liveness + возраст фида кампаний.
func (h *Handler) Healthz(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status":       "ok",
		"campaigns":    h.store.Len(),
		"feed_age_sec": int(h.store.Age().Seconds()),
	})
}
