package rtb

// RTB hot path — порт 1:1 backend/routers/rtb.py::process_bid_request.
// Контракт эндпоинтов и Redis-ключей идентичен Python (см. parity_matrix.md):
// бид, выигранный Go-биддером, биллится Python-бэкендом через bidmeta:{bid_id}.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/redisx"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/shading"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/targeting"
)

// Handler обслуживает RTB hot path.
type Handler struct {
	store   *feed.Store
	redis   *redisx.Client
	shading *shading.Store
	base    string // базовый URL backend'а для nurl/imp/click
	seat    string
	metrics *metrics.Registry // nil-safe: без реестра метрики не пишутся
}

// SetMetrics подключает реестр метрик (вызывается из main до старта сервера).
func (h *Handler) SetMetrics(r *metrics.Registry) { h.metrics = r }

// MaxBidRequestBytes — верхняя граница тела OpenRTB-запроса. Типичный запрос
// с несколькими imp, device, user.eids и ext — 5-50 КиБ; 256 КиБ покрывает
// самые «жирные» SSP и при этом не даёт одному партнёру раздувать память.
const MaxBidRequestBytes = 256 << 10

// Бюджет времени на Redis-вызовы внутри одного бид-запроса.
//   - defaultBidBudget используется, когда SSP не прислал tmax;
//   - tmaxNetworkReserve вычитается из tmax SSP (сеть + сериализация ответа);
//   - minBidBudget — нижняя граница: при tmax ниже её продолжаем работать
//     с минимальным бюджетом, а не с нулевым (иначе каждый вызов — таймаут).
const (
	defaultBidBudget   = 80 * time.Millisecond
	tmaxNetworkReserve = 20 * time.Millisecond
	minBidBudget       = 15 * time.Millisecond
)

// bidDeadline переводит tmax (мс, OpenRTB 2.x) в бюджет контекста.
func bidDeadline(tmaxMs int) time.Duration {
	if tmaxMs <= 0 {
		return defaultBidBudget
	}
	budget := time.Duration(tmaxMs)*time.Millisecond - tmaxNetworkReserve
	if budget < minBidBudget {
		return minBidBudget
	}
	if budget > defaultBidBudget {
		// Больше дефолта не берём: SLA биддера — p99 < 80 мс независимо от tmax.
		return defaultBidBudget
	}
	return budget
}

// m*-хелперы nil-safe: hot path не проверяет наличие реестра в каждом месте.
func (h *Handler) mNoBid(reason metrics.NoBidReason) {
	if h.metrics != nil {
		h.metrics.IncNoBid(reason)
	}
}

func (h *Handler) mGateError() {
	if h.metrics != nil {
		h.metrics.IncGateError()
	}
}

// NewHandler создаёт handler со всеми зависимостями hot path.
func NewHandler(store *feed.Store, rc *redisx.Client) *Handler {
	base := os.Getenv("EVENT_BASE_URL")
	if base == "" {
		base = os.Getenv("BACKEND_BASE_URL")
	}
	if base == "" {
		base = "http://localhost:3000"
	}
	h := &Handler{
		store: store,
		redis: rc,
		base:  strings.TrimRight(base, "/"),
		seat:  "central-dsp",
	}
	if rc != nil {
		h.shading = shading.NewStore(rc.Raw())
	}
	return h
}

type scoredCandidate struct {
	c     *feed.Candidate
	price float64 // эффективная цена после shading
	raw   float64 // цена до shading
}

// Bid — POST /rtb/bid/{endpointKey}. Пайплайн — паритет с Python:
// auth -> QPS -> parse -> targeting -> freq (check-only) -> budget reserve ->
// shading (только at=1) -> adm с erid -> bidmeta/bidfreq в Redis -> response.
func (h *Handler) Bid(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	ctx := r.Context()
	if h.metrics != nil {
		h.metrics.IncBidRequest()
		defer func() { h.metrics.ObserveLatency(time.Since(start).Seconds()) }()
	}

	// 1. Auth: endpointKey -> supply partner (+ опциональный X-CentralDSP-Token,
	//    сверяется по sha256-хэшу из фида, см. partner_auth.go).
	snap := h.store.Snapshot()
	if snap == nil {
		h.mNoBid(metrics.ReasonFeedNotLoaded)
		http.Error(w, "feed not loaded", http.StatusServiceUnavailable)
		return
	}
	partner := snap.Partners[r.PathValue("endpointKey")]
	if partner == nil || partner.Status != "active" {
		h.mNoBid(metrics.ReasonUnknownEndpoint)
		http.Error(w, "unknown endpoint", http.StatusNotFound)
		return
	}
	if !partnerTokenOK(partner, r) {
		h.mNoBid(metrics.ReasonBadAuth)
		http.Error(w, "invalid ssp token", http.StatusForbidden)
		return
	}

	// 2. QPS-гейт партнёра. Ошибка Redis — fail-open (биддим), но считаем
	//    gate_errors, чтобы деградация Redis была видна в /metrics.
	if h.redis != nil && partner.QPSLimit > 0 {
		ok, err := h.redis.CheckQPS(ctx, partner.ID, int64(partner.QPSLimit), time.Now().Unix())
		if err != nil {
			h.mGateError()
		} else if !ok {
			h.mNoBid(metrics.ReasonQPSExceeded)
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
	}

	// 3. Parse/validate. Тело ограничено MaxBidRequestBytes: OpenRTB-запрос
	//    редко больше 30-50 КиБ, а без лимита партнёр (или атакующий) может
	//    заставить биддер аллоцировать произвольный объём.
	r.Body = http.MaxBytesReader(w, r.Body, MaxBidRequestBytes)
	var req BidRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			h.mNoBid(metrics.ReasonParseError)
			http.Error(w, "bid request too large", http.StatusRequestEntityTooLarge)
			return
		}
		h.mNoBid(metrics.ReasonParseError)
		http.Error(w, "invalid bid request", http.StatusBadRequest)
		return
	}
	if req.ID == "" || len(req.Imp) == 0 {
		h.mNoBid(metrics.ReasonEmptyRequest)
		w.WriteHeader(http.StatusNoContent)
		return
	}

	// tmax SSP -> дедлайн контекста для Redis-вызовов (freq/budget/shading).
	// Оставляем запас на сеть до SSP и сериализацию ответа; если tmax не задан
	// или неразумно мал, работаем с дефолтным бюджетом.
	if budget := bidDeadline(req.TMax); budget > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, budget)
		defer cancel()
	}
	auctionType := req.At
	if auctionType == 0 {
		auctionType = 2 // OpenRTB default: second price
	}

	// Central DSP биддит только в RUB.
	if len(req.Cur) > 0 && !containsStr(req.Cur, "RUB") {
		h.mNoBid(metrics.ReasonCurrency)
		w.WriteHeader(http.StatusNoContent)
		return
	}

	view := buildRequestView(&req)
	deviceID := deviceIDOf(&req)

	var bids []Bid
	for i := range req.Imp {
		imp := &req.Imp[i]
		if b := h.bidOnImp(ctx, &req, imp, view, deviceID, partner, snap, auctionType); b != nil {
			bids = append(bids, *b)
		}
	}

	if len(bids) == 0 {
		h.mNoBid(metrics.ReasonNoCandidates)
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if h.metrics != nil {
		var sum float64
		for i := range bids {
			sum += bids[i].Price
		}
		h.metrics.IncBidResponse(len(bids), sum)
	}
	resp := BidResponse{
		ID:      req.ID,
		Cur:     "RUB",
		SeatBid: []SeatBid{{Seat: h.seat, Bid: bids}},
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
	slog.Debug("bid", "id", req.ID, "bids", len(bids), "took", time.Since(start))
}

// bidOnImp — кандидаты одного impression: фильтры + выбор лучшей цены.
func (h *Handler) bidOnImp(ctx interface{ Done() <-chan struct{} }, req *BidRequest, imp *Imp, view *targeting.RequestView, deviceID string, partner *feed.Partner, snap *feed.Snapshot, auctionType int) *Bid {
	rctx, ok := ctx.(interface {
		Done() <-chan struct{}
		Err() error
		Deadline() (time.Time, bool)
		Value(any) any
	})
	if !ok {
		return nil
	}

	adType := impAdType(imp)
	if adType == "" {
		return nil
	}
	if partner.AllowedTypes != nil && len(partner.AllowedTypes) > 0 && !partner.AllowedTypes[adType] {
		return nil
	}

	floor := imp.BidFloor
	if partner.BidFloor > floor {
		floor = partner.BidFloor
	}

	// Собираем прошедших таргетинг кандидатов, сортируем по цене (desc).
	impView := buildImpView(imp)
	var scored []scoredCandidate
	for i := range snap.Candidates {
		c := &snap.Candidates[i]
		if !typeMatches(c, adType, imp) {
			continue
		}
		if !h.matchCandidate(c, req, view, partner, snap) {
			continue
		}
		if c.T != nil && !targeting.MatchImp(feedImpTargeting(c.T), impView) {
			continue
		}
		raw := c.BidPrice
		if c.MaxBid != nil && *c.MaxBid > 0 && raw > *c.MaxBid {
			raw = *c.MaxBid
		}
		price := raw
		if h.shading != nil && shading.ShouldShade(c.BidShading, auctionType) {
			factor := h.shading.GetFactor(rctx, c.CampaignID, partner.ID)
			price = shading.Apply(raw, factor, floor)
		}
		if price < floor || price <= 0 {
			continue
		}
		scored = append(scored, scoredCandidate{c: c, price: price, raw: raw})
	}
	sort.Slice(scored, func(i, j int) bool { return scored[i].price > scored[j].price })

	for _, sc := range scored {
		c := sc.c
		// Bid-time gate: freq-окна (read-only) + атомарная резервация бюджета
		// кампании И line item в одном Lua-роундтрипе — порт gate_and_reserve.
		// Инкремент freq-счётчиков делает backend на /imp через bidfreq:{bid_id}.
		capsList := parseCaps(c)
		if h.redis != nil {
			cost := sc.price / 1000.0 // CPM -> цена одного показа
			liBudget := 0.0
			if c.LiBudget != nil {
				liBudget = *c.LiBudget
			}
			okRes, err := h.redis.GateAndReserve(rctx, c.CampaignID, c.LiID, cost, c.CampBudget, liBudget, deviceID, capsList)
			if err != nil {
				if h.metrics != nil {
					h.metrics.IncGateError()
				}
				slog.Error("gate_and_reserve", "err", err)
				continue
			}
			if !okRes {
				if h.metrics != nil {
					h.metrics.IncGateReject()
				}
				continue
			}
		}

		bidID := uuid.NewString()
		token, err := SignEventToken(bidID, DefaultTokenTTL)
		if err != nil {
			slog.Error("sign token", "err", err)
			return nil
		}
		h.persistBidMeta(rctx, bidID, imp, c, partner, sc.price, view, adType, deviceID, capsList)

		adm := BuildAdm(c, h.base, token)
		nurl := fmt.Sprintf("%s/api/rtb/win?bid=%s&price=${AUCTION_PRICE}", h.base, token)
		w, hgt := 0, 0
		if c.Width != nil {
			w = *c.Width
		}
		if c.Height != nil {
			hgt = *c.Height
		}
		ext, _ := json.Marshal(map[string]string{"erid": c.Erid})
		dealID := ""
		if c.T != nil {
			dealID = targeting.MatchedDeal(c.T.DealIDs, impView.DealIDs)
		}
		return &Bid{
			ID: bidID, ImpID: imp.ID, Price: roundPrice(sc.price),
			AdM: adm, ADomain: admomains(c), CrID: c.CreativeID,
			NURL: nurl, W: w, H: hgt, DealID: dealID, Ext: ext,
		}
	}
	return nil
}

// matchCandidate — таргетинг line item + SSP allowlist + глобальные домен-листы.
func (h *Handler) matchCandidate(c *feed.Candidate, req *BidRequest, view *targeting.RequestView, partner *feed.Partner, snap *feed.Snapshot) bool {
	// badv: домен рекламодателя не должен быть заблокирован SSP.
	if len(req.BAdv) > 0 && c.AdvDomain != "" {
		for _, b := range req.BAdv {
			if strings.EqualFold(strings.TrimPrefix(b, "www."), strings.TrimPrefix(c.AdvDomain, "www.")) {
				return false
			}
		}
	}
	// Глобальные white/blacklist владельца (owner_id -> domains).
	owner := c.OwnerID
	if view.Domain != "" {
		if bl := snap.GBlack[owner]; bl != nil && bl[view.Domain] {
			return false
		}
		if wl := snap.GWhite[owner]; len(wl) > 0 && !wl[view.Domain] {
			return false
		}
	}
	t := c.T
	if t == nil {
		return true
	}
	// SSP allowlist проверяет вызывающая сторона (как в Python).
	if len(t.SSPAllowlist) > 0 {
		allowed := false
		for _, id := range t.SSPAllowlist {
			if id == partner.ID {
				allowed = true
				break
			}
		}
		if !allowed {
			return false
		}
	}
	return targeting.Match(feedTargetingToMatch(t), view)
}

// parseCaps — порт _parse_freq_caps: JSONB frequency_caps (объекты
// {impressions, period_hours}) с фолбэком на легаси-поля кампании.
func parseCaps(c *feed.Candidate) []redisx.CapWindow {
	var caps []redisx.CapWindow
	if len(c.FreqCaps) > 0 && string(c.FreqCaps) != "null" {
		var raw []redisx.CapWindow
		if json.Unmarshal(c.FreqCaps, &raw) == nil {
			for _, w := range raw {
				if w.Limit > 0 && w.PeriodHours > 0 && len(caps) < 3 {
					caps = append(caps, w)
				}
			}
		}
	}
	if len(caps) == 0 && c.FreqCapImps != nil && *c.FreqCapImps > 0 {
		hrs := 24
		if c.FreqCapHours != nil && *c.FreqCapHours > 0 {
			hrs = *c.FreqCapHours
		}
		caps = append(caps, redisx.CapWindow{Limit: *c.FreqCapImps, PeriodHours: hrs})
	}
	return caps
}

// persistBidMeta пишет bidmeta:{bid_id} и bidfreq:{bid_id} — формат Python.
func (h *Handler) persistBidMeta(ctx interface {
	Done() <-chan struct{}
	Err() error
	Deadline() (time.Time, bool)
	Value(any) any
}, bidID string, imp *Imp, c *feed.Candidate, partner *feed.Partner, price float64, view *targeting.RequestView, adType, deviceID string, caps []redisx.CapWindow) {
	if h.redis == nil {
		return
	}
	rdb := h.redis.Raw()
	dt := targeting.DeviceTypes[view.DeviceType]
	if dt == "" {
		dt = "unknown"
	}
	meta, _ := json.Marshal(map[string]any{
		"bid_id": bidID, "imp_id": imp.ID,
		"campaign_id": c.CampaignID, "line_item_id": c.LiID,
		"creative_id": c.CreativeID, "supply_partner_id": partner.ID,
		"price":       roundPrice(price),
		"device_type": dt, "geo_country": view.Country, "ad_type": adType,
		"domain": view.Domain, "os": view.OS, "browser": "",
	})
	rdb.Set(ctx, "bidmeta:"+bidID, meta, time.Hour)
	if deviceID != "" && len(caps) > 0 {
		capsJSON, _ := json.Marshal(caps)
		rdb.Set(ctx, "bidfreq:"+bidID, deviceID+"|"+string(capsJSON), time.Hour)
	}
	// Retargeting bridge: clid == bid_id после клика.
	if deviceID != "" {
		rdb.Set(ctx, "clid:"+bidID, deviceID, 24*time.Hour)
	}
}

// Win — POST /rtb/win (внутренний; в prod win идёт на backend /api/rtb/win).
func (h *Handler) Win(w http.ResponseWriter, r *http.Request) {
	if VerifyEventToken(r.URL.Query().Get("bid")) == "" {
		http.Error(w, "invalid event token", http.StatusForbidden)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// Loss — POST /rtb/loss: освобождение бюджетного резерва.
func (h *Handler) Loss(w http.ResponseWriter, r *http.Request) {
	bidID := VerifyEventToken(r.URL.Query().Get("bid"))
	if bidID == "" {
		http.Error(w, "invalid event token", http.StatusForbidden)
		return
	}
	if h.redis != nil {
		raw, err := h.redis.Raw().Get(r.Context(), "bidmeta:"+bidID).Result()
		if err == nil {
			var meta struct {
				CampaignID string  `json:"campaign_id"`
				LineItemID string  `json:"line_item_id"`
				Price      float64 `json:"price"`
			}
			if json.Unmarshal([]byte(raw), &meta) == nil && meta.CampaignID != "" {
				_ = h.redis.ReleaseGate(r.Context(), meta.CampaignID, meta.LineItemID, meta.Price/1000.0)
				if h.metrics != nil {
					h.metrics.IncLossRelease()
				}
			}
		}
	}
	w.WriteHeader(http.StatusNoContent)
}

// Imp — POST /rtb/imp (в prod imp идёт на backend /api/rtb/imp).
func (h *Handler) Imp(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusNoContent)
}

// Healthz — GET /healthz: liveness + возраст фида.
func (h *Handler) Healthz(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	redisOK := false
	if h.redis != nil && h.redis.Ping(r.Context()) == nil {
		redisOK = true
	}
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status":       "ok",
		"campaigns":    h.store.Len(),
		"feed_age_sec": int(h.store.Age().Seconds()),
		"redis":        redisOK,
	})
}

// --- helpers ---

func buildRequestView(req *BidRequest) *targeting.RequestView {
	v := &targeting.RequestView{Now: time.Now(), Env: "site"}
	if d := req.Device; d != nil {
		v.UA = strings.ToLower(d.UA)
		v.OS = targeting.DeviceOS(d.OS, d.UA)
		v.OSV = strings.TrimSpace(d.OSV)
		v.DeviceType = d.DeviceType
		v.Language = strings.ToLower(d.Language)
		v.ConnType = d.ConnectionType
		v.Carrier = strings.ToLower(strings.TrimSpace(d.Carrier))
		v.IP = d.IP
		if v.IP == "" {
			v.IP = d.IPv6
		}
		v.DNT = d.DNT == 1 || d.LMT == 1
		if d.Geo != nil {
			v.Country = strings.ToUpper(d.Geo.Country)
			v.Region = strings.ToLower(d.Geo.Region)
			v.City = strings.ToLower(d.Geo.City)
		}
	}
	// Python: site = req.site or req.app — приоритет у site, если переданы оба.
	var pub *Publisher
	var content *Content
	var kw, page string
	switch {
	case req.Site != nil:
		s := req.Site
		v.Domain = strings.ToLower(s.Domain)
		v.SiteCats = s.Cat
		pub, content, kw, page = s.Publisher, s.Content, s.Keywords, s.Page
		if req.App != nil {
			v.Env = "app"
			v.Bundle = strings.ToLower(req.App.Bundle)
		}
	case req.App != nil:
		a := req.App
		v.Env = "app"
		v.Bundle = strings.ToLower(a.Bundle)
		// domain для allow/block-листов: Python берёт site.domain or site.bundle из объекта app.
		v.Domain = strings.ToLower(a.Domain)
		if v.Domain == "" {
			v.Domain = v.Bundle
		}
		v.SiteCats = a.Cat
		pub, content, kw = a.Publisher, a.Content, a.Keywords
	}
	if pub != nil {
		v.PubID = strings.TrimSpace(pub.ID)
	}
	var cKw, cTitle string
	if content != nil {
		cKw, cTitle = content.Keywords, content.Title
	}
	v.Keywords = targeting.KeywordHaystack(kw, cKw, page, cTitle)
	if u := req.User; u != nil {
		v.Gender = strings.ToUpper(strings.TrimSpace(u.Gender))
		v.YOB = u.YOB
	}
	if r := req.Regs; r != nil {
		v.COPPA = r.COPPA == 1
		v.GDPR = r.GDPR == 1
		if !v.GDPR && len(r.Ext) > 0 {
			var ext struct {
				GDPR int `json:"gdpr"`
			}
			if json.Unmarshal(r.Ext, &ext) == nil && ext.GDPR == 1 {
				v.GDPR = true
			}
		}
	}
	return v
}

// buildImpView — плоское представление imp для targeting.MatchImp (один раз на imp).
func buildImpView(imp *Imp) *targeting.ImpView {
	v := &targeting.ImpView{
		Interstitial: imp.Instl == 1,
		Secure:       imp.Secure == 1,
		Rewarded:     imp.Rwdd == 1,
	}
	if imp.Banner != nil {
		v.Pos = imp.Banner.Pos
	} else if imp.Video != nil {
		v.Pos = imp.Video.Pos
	}
	if imp.Video != nil {
		v.HasVideo = true
		v.VideoMaxDuration = imp.Video.MaxDuration
		v.VideoSkippable = imp.Video.Skip == 1
	}
	if !v.Rewarded && len(imp.Ext) > 0 {
		var ext struct {
			Rewarded int `json:"rewarded"`
		}
		if json.Unmarshal(imp.Ext, &ext) == nil && ext.Rewarded == 1 {
			v.Rewarded = true
		}
	}
	if imp.PMP != nil {
		for _, d := range imp.PMP.Deals {
			if d.ID != "" {
				v.DealIDs = append(v.DealIDs, d.ID)
			}
		}
	}
	return v
}

func deviceIDOf(req *BidRequest) string {
	if req.Device != nil && req.Device.IFA != "" {
		return req.Device.IFA
	}
	if req.User != nil {
		// user.eids (UID2/ID5) — приоритетнее анонимного user.id.
		for _, eid := range req.User.EIDs {
			for _, uid := range eid.UIDs {
				if uid.ID != "" {
					return uid.ID
				}
			}
		}
		if req.User.ID != "" {
			return req.User.ID
		}
		if req.User.BuyerUID != "" {
			return req.User.BuyerUID
		}
	}
	return ""
}

func impAdType(imp *Imp) string {
	extPush := false
	if len(imp.Ext) > 0 {
		var ext struct {
			Push     any    `json:"push"`
			AdFormat string `json:"ad_format"`
		}
		if json.Unmarshal(imp.Ext, &ext) == nil {
			extPush = ext.Push != nil || ext.AdFormat == "push"
		}
	}
	return targeting.ImpAdType(imp.Banner != nil, imp.Native != nil, imp.Video != nil, extPush)
}

func typeMatches(c *feed.Candidate, adType string, imp *Imp) bool {
	if c.Type != adType {
		return false
	}
	// Баннер: точный размер или формат из списка.
	if adType == "banner" && imp.Banner != nil && c.Width != nil && c.Height != nil {
		b := imp.Banner
		if b.W > 0 && b.H > 0 && (b.W == *c.Width && b.H == *c.Height) {
			return true
		}
		for _, f := range b.Format {
			if f.W == *c.Width && f.H == *c.Height {
				return true
			}
		}
		return b.W == 0 && len(b.Format) == 0 // размер не указан: пропускаем
	}
	return true
}

// feedTargetingToMatch — request-level часть таргетинга line item. Должна
// покрывать ВСЕ ключи, которые редактирует кабинет (frontend/components/targeting-editor.tsx):
// поле, не переданное сюда, молча игнорируется биддером.
func feedTargetingToMatch(t *feed.Targeting) *targeting.Targeting {
	return &targeting.Targeting{
		GeoCountries: t.GeoCountries, GeoRegions: t.GeoRegions, GeoCities: t.GeoCities,
		DeviceTypes: t.DeviceTypes, OS: t.OS, Browsers: t.Browsers, Languages: t.Languages,
		ConnectionTypes: t.ConnectionTypes, IABCategories: t.IABCategories, IABBlock: t.IABBlock,
		DomainAllowlist: t.DomainAllowlist, DomainBlocklist: t.DomainBlocklist,
		AgeMin: t.AgeMin, AgeMax: t.AgeMax, Genders: t.Genders,
		Hours: t.Hours, Days: t.Days,
		TrafficTypes: t.TrafficTypes, Carriers: t.Carriers, OSVersionMin: t.OSVersionMin,
		AppBundlesBlock: t.AppBundlesBlock, AppBundlesAllow: t.AppBundlesAllow,
		PublisherBlock: t.PublisherBlock, PublisherAllow: t.PublisherAllow,
		KeywordsBlock: t.KeywordsBlock, KeywordsAllow: t.KeywordsAllow,
		IPRanges:   t.IPRanges,
		BlockCOPPA: t.BlockCOPPA, BlockGDPR: t.BlockGDPR, SkipDNT: t.SkipDNT,
	}
}

// feedImpTargeting — imp-level часть таргетинга line item.
func feedImpTargeting(t *feed.Targeting) *targeting.ImpTargeting {
	return &targeting.ImpTargeting{
		BannerPositions:  t.BannerPositions,
		InterstitialOnly: t.InterstitialOnly, ExcludeInterstitial: t.ExcludeInterstitial,
		RewardedOnly: t.RewardedOnly, SecureOnly: t.SecureOnly,
		VideoMinDuration: t.VideoMinDuration, VideoSkippableOnly: t.VideoSkippableOnly,
		DealIDs: t.DealIDs,
	}
}

func admomains(c *feed.Candidate) []string {
	if c.AdvDomain != "" {
		return []string{c.AdvDomain}
	}
	return nil
}

func roundPrice(p float64) float64 {
	return float64(int64(p*10000+0.5)) / 10000
}

func containsStr(list []string, v string) bool {
	for _, x := range list {
		if strings.EqualFold(x, v) {
			return true
		}
	}
	return false
}
