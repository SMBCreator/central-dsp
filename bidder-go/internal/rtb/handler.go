package rtb

// RTB hot path — порт 1:1 backend/routers/rtb.py::process_bid_request.
// Контракт эндпоинтов и Redis-ключей идентичен Python (см. parity_matrix.md):
// бид, выигранный Go-биддером, биллится Python-бэкендом через bidmeta:{bid_id}.

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"net"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/prefilter"
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
	// internalToken — RTB_API_TOKEN: им backend подписывает автопроверку SSP
	// (ssp_check.py), чтобы обойти IP-whitelist партнёра. "" = обход выключен.
	internalToken string
	// trustedProxies — сети (TRUSTED_PROXIES), с которых принимаются
	// X-Forwarded-For / X-Real-IP; иначе клиент = RemoteAddr (audit S-06).
	trustedProxies []*net.IPNet
	// prefilter — O(1)-отсев ботов/чужого гео/datacenter-IP до таргетинга
	// (audit R-03, порт backend/prefilter.py). nil = выключен.
	prefilter *prefilter.Filter
}

// SetMetrics подключает реестр метрик (вызывается из main до старта сервера).
func (h *Handler) SetMetrics(r *metrics.Registry) { h.metrics = r }

// SetPrefilter подключает предфильтр (см. internal/prefilter).
func (h *Handler) SetPrefilter(f *prefilter.Filter) { h.prefilter = f }

// SetInternalToken задаёт секрет для заголовка internalCheckHeader.
func (h *Handler) SetInternalToken(t string) { h.internalToken = t }

// internalCheckHeader — заголовок автопроверки из кабинета. Значение = RTB_API_TOKEN.
const internalCheckHeader = "X-CentralDSP-Internal-Check"

// skipTokenHeader — "1" вместе с валидным internalCheckHeader: не сверять
// SSP-токен партнёра (открытого токена у backend'а нет). См. Bid.
const skipTokenHeader = "X-CentralDSP-Skip-Token"

func (h *Handler) isInternalCheck(r *http.Request) bool {
	if h.internalToken == "" {
		return false
	}
	given := r.Header.Get(internalCheckHeader)
	return given != "" && subtle.ConstantTimeCompare([]byte(given), []byte(h.internalToken)) == 1
}

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
	pctr  float64 // pCTR, если модель повлияла на цену (decision log); 0 = нет
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
	if snap == nil || snap.LoadedAt.IsZero() {
		h.mNoBid(metrics.ReasonFeedNotLoaded)
		http.Error(w, "feed not loaded", http.StatusServiceUnavailable)
		return
	}
	// Audit R-02: снапшот старше FEED_MAX_AGE — не торгуем. Иначе пауза кампании,
	// исчерпанный бюджет, блокировка SSP или отозванный токен не применяются,
	// пока backend недоступен. 503 — SSP видит недоступность и снижает трафик.
	if !h.store.Fresh() {
		h.mNoBid(metrics.ReasonFeedStale)
		http.Error(w, "feed stale", http.StatusServiceUnavailable)
		return
	}
	// Паритет с Python (rtb.py): партнёр торгуется в статусах active и testing.
	// testing = онбординг: реальные биды, чтобы SSP видел ответы; deny-статусы
	// (draft/paused/blocked) фид не отдаёт вовсе (bidder_feed.py).
	partner := snap.Partners[r.PathValue("endpointKey")]
	if partner == nil || !partnerBiddable(partner.Status) {
		h.mNoBid(metrics.ReasonUnknownEndpoint)
		http.Error(w, "unknown endpoint", http.StatusNotFound)
		return
	}
	// Автопроверка из кабинета (ssp_check.py) идёт с IP backend'а и без открытого
	// SSP-токена (в БД только хэш, миграция 032). Поэтому с валидным internal-
	// токеном (RTB_API_TOKEN) она может: (а) пропустить IP-whitelist — как
	// client_ip=None в Python; (б) по заголовку X-CentralDSP-Skip-Token пропустить
	// сверку токена партнёра — это verify_token=False в Python. Негативный тест
	// auth шлёт явный неверный токен без Skip-Token и должен получить 403.
	//
	// Audit S-06: партнёр без выданного/с отозванным токеном закрыт ВСЕГДА —
	// даже для автопроверки (паритет с rtb.py: 403 «SSP token not issued» до
	// verify_token). Невалидный ip_whitelist — тоже 403 (fail-closed).
	internal := h.isInternalCheck(r)
	skipToken := internal && r.Header.Get(skipTokenHeader) == "1" && r.Header.Get(TokenHeader) == ""
	switch verdict := checkPartnerToken(partner, r, time.Now()); {
	case verdict == tokenNoHash:
		h.mNoBid(metrics.ReasonTokenNotIssued)
		http.Error(w, "ssp token not issued", http.StatusForbidden)
		return
	case verdict == tokenMismatch && !skipToken:
		h.mNoBid(metrics.ReasonBadAuth)
		http.Error(w, "invalid ssp token", http.StatusForbidden)
		return
	}
	if partner.WhitelistInvalid {
		h.mNoBid(metrics.ReasonWhitelistInvalid)
		http.Error(w, "partner ip whitelist invalid", http.StatusForbidden)
		return
	}
	if !internal && !h.partnerIPOK(partner, r) {
		h.mNoBid(metrics.ReasonIPDenied)
		http.Error(w, "ip not allowed", http.StatusForbidden)
		return
	}
	endpointKey := r.PathValue("endpointKey")
	if h.metrics != nil {
		h.metrics.IncPartnerRequest(endpointKey)
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

	// Предфильтр (audit R-03): как в Python — после парсинга, до таргетинга,
	// Redis и аукциона. Отсечение считается отдельно от no-bid.
	if h.prefilter != nil {
		if reason := h.prefilter.Check(prefilterDevice(req.Device)); reason != "" {
			if h.metrics != nil {
				h.metrics.IncPrefilterDrop(string(reason))
			}
			w.WriteHeader(http.StatusNoContent)
			return
		}
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
		h.metrics.IncPartnerBids(endpointKey, len(bids))
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

// partnerBiddable — статусы SSP-партнёра, при которых биддер отвечает на запросы.
// Совпадает с фильтром фида (bidder_feed.py) и Python-биддером.
func partnerBiddable(status string) bool {
	return status == "active" || status == "testing"
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
		if !h.matchCandidate(c, req, view, partner, snap, adType) {
			continue
		}
		if c.T != nil && !targeting.MatchImp(feedImpTargeting(c.T), impView) {
			continue
		}
		// Ретаргетинг-сегменты — единственный Redis-вызов среди фильтров, поэтому
		// после всех in-memory проверок; у большинства line items сегментов нет.
		if c.T != nil && !h.audienceOK(rctx, c.T.AudienceInclude, c.T.AudienceExclude, deviceID) {
			continue
		}
		raw := c.BidPrice
		if c.MaxBid != nil && *c.MaxBid > 0 && raw > *c.MaxBid {
			raw = *c.MaxBid
		}
		// Python отсекает price < floor до pCTR и shading: множитель не может
		// «спасти» ставку ниже пола.
		if raw < floor {
			continue
		}
		price := raw
		// Value-based биддинг (Часть B, порт rtb.py): только objective
		// traffic/conversions и bid_type CPM; множитель pctr/baseline зажат
		// в [0.7, 1.3]; floor и max_bid соблюдаются; нет модели/совпадений —
		// базовая цена.
		var pctrUsed float64
		if snap.PCTR != nil && (c.Objective == "traffic" || c.Objective == "conversions") &&
			(c.BidType == "" || c.BidType == "cpm") {
			pred := snap.PCTR.Predict(view.Domain, c.CreativeID, deviceTypeName(view.DeviceType), view.Now.UTC().Hour())
			if mult, ok := pred.Multiplier(); ok {
				price = math.Max(floor, price*mult)
				if c.MaxBid != nil && *c.MaxBid > 0 && price > *c.MaxBid {
					price = *c.MaxBid
				}
				pctrUsed = pred.PCTR
			}
		}
		if h.shading != nil && shading.ShouldShade(c.BidShading, auctionType) {
			factor := h.shading.GetFactor(rctx, c.CampaignID, partner.ID)
			price = shading.Apply(price, factor, floor)
		}
		if price < floor || price <= 0 {
			continue
		}
		scored = append(scored, scoredCandidate{c: c, price: price, raw: raw, pctr: pctrUsed})
	}
	sort.Slice(scored, func(i, j int) bool { return scored[i].price > scored[j].price })

	for _, sc := range scored {
		c := sc.c
		// Bid-time gate: freq-окна (read-only) + атомарная резервация бюджета
		// кампании И line item в одном Lua-роундтрипе — порт gate_and_reserve.
		// Инкремент freq-счётчиков делает backend на /imp через bidfreq:{bid_id}.
		capsList := parseCaps(c)
		bidID := uuid.NewString()
		// test=1 (OpenRTB 3.2.1): SSP проверяет интеграцию — отвечаем реальным
		// бидом, но бюджет не резервируем и деньги не списываем (is_test в bidmeta).
		var reservation redisx.BidReservation
		if h.redis != nil && req.Test != 1 {
			liBudget := 0.0
			if c.LiBudget != nil {
				liBudget = *c.LiBudget
			}
			// Audit M-05: одним Lua — дневные бюджеты кампании и группы, доступный
			// баланс рекламодателя и остаток общего бюджета; бид попадает в
			// pending:bids до win/loss/expire (M-06).
			res, okRes, err := h.redis.Reserve(rctx, time.Now(), redisx.ReserveInput{
				BidID: bidID, CampaignID: c.CampaignID, LiID: c.LiID, AdvertiserID: c.AdvertiserID,
				Cost: sc.price / 1000.0, CampBudget: c.CampBudget, LiBudget: liBudget,
				AdvBalance: c.AdvBalance, TotalRemaining: c.TotalBudget - c.SpentTotal, HasTotal: c.TotalBudget > 0,
				DeviceID: deviceID, Caps: capsList,
			})
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
			reservation = res
		}

		token, err := SignEventToken(bidID, DefaultTokenTTL)
		if err != nil {
			slog.Error("sign token", "err", err)
			h.abortReservation(rctx, reservation)
			return nil
		}
		// Audit R-02: bidmeta + событие bid — обязательный bookkeeping. Без bidmeta
		// backend не опознает win/imp/click (rtb.py:_find_bid) — платный показ
		// пройдёт без списания и без учёта. Не удалось записать — резерв
		// возвращаем и бид не отдаём.
		if err := h.persistBidMeta(rctx, bidID, imp, c, partner, sc.price, sc.pctr, view, adType, deviceID, capsList, req.Test == 1, reservation); err != nil {
			slog.Error("bid bookkeeping failed; no-bid", "bid_id", bidID, "err", err)
			if h.metrics != nil {
				h.metrics.IncBookkeepingError()
			}
			h.abortReservation(rctx, reservation)
			return nil
		}

		adm := BuildAdm(c, h.base, token)
		nurl := fmt.Sprintf("%s/api/rtb/win?bid=%s&price=${AUCTION_PRICE}", h.base, token)
		// lurl (OpenRTB 2.5 §4.2.3): SSP сообщает о проигрыше с кодом причины и
		// ценой, которой не хватило. Идёт в Go (/rtb/* -> биддер): освобождаем
		// бюджетный резерв сразу и пишем событие loss для отчёта «почему проигрываем».
		lurl := fmt.Sprintf("%s/rtb/loss?bid=%s&reason=${AUCTION_LOSS}&mtw=${AUCTION_MIN_TO_WIN}", h.base, token)
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
			Cat: c.IABCategories, Attr: c.Attrs,
			NURL: nurl, LURL: lurl, W: w, H: hgt, DealID: dealID, Ext: ext,
		}
	}
	return nil
}

// matchCandidate — таргетинг line item + SSP allowlist + глобальные домен-листы
// + решения оптимизатора / clean traffic / scoring-профиля (audit R-03).
func (h *Handler) matchCandidate(c *feed.Candidate, req *BidRequest, view *targeting.RequestView, partner *feed.Partner, snap *feed.Snapshot, adType string) bool {
	// badv: домен рекламодателя не должен быть заблокирован SSP.
	if len(req.BAdv) > 0 && c.AdvDomain != "" {
		for _, b := range req.BAdv {
			if strings.EqualFold(strings.TrimPrefix(b, "www."), strings.TrimPrefix(c.AdvDomain, "www.")) {
				return false
			}
		}
	}
	// bcat: издатель запретил IAB-категории. Совпадение по коду или по его
	// tier-1 родителю (bcat "IAB7" блокирует креатив "IAB7-3"). Креатив без
	// заявленных категорий пропускаем — иначе весь инвентарь с bcat отвалится.
	if len(req.BCat) > 0 && len(c.IABCategories) > 0 && iabBlocked(req.BCat, c.IABCategories) {
		return false
	}
	// Блоки оптимизатора (optimizer_blocks): любое наблюдаемое измерение — те же
	// десять ключей и та же нормализация, что в rtb.py (camp_blocks).
	if byType := snap.OptBlocks[c.CampaignID]; byType != nil {
		if snap.Blocked(c.CampaignID, "domain", view.Domain) ||
			snap.Blocked(c.CampaignID, "supply_partner", partner.ID) ||
			snap.Blocked(c.CampaignID, "device_type", deviceTypeName(view.DeviceType)) ||
			snap.Blocked(c.CampaignID, "geo_country", view.Country) ||
			snap.Blocked(c.CampaignID, "os", view.OS) ||
			snap.Blocked(c.CampaignID, "browser", targeting.DeviceBrowser(view.UA)) ||
			snap.Blocked(c.CampaignID, "ad_type", adType) ||
			snap.Blocked(c.CampaignID, "creative", c.CreativeID) ||
			snap.Blocked(c.CampaignID, "line_item", c.LiID) ||
			snap.Blocked(c.CampaignID, "hour", strconv.Itoa(view.Now.UTC().Hour())) {
			return false
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
		// Clean traffic (тумблер кампании): домен с низким quality score.
		// Только opt-in и только известные домены — fail-open, как в Python.
		if c.CleanTrafficOnly && snap.DirtyDomains[view.Domain] {
			return false
		}
		// Scoring-профиль кампании в режиме enforce: домен ниже порога.
		if sb := snap.ScoringBlocks[c.CampaignID]; sb != nil && sb[view.Domain] {
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
}, bidID string, imp *Imp, c *feed.Candidate, partner *feed.Partner, price, pctrUsed float64, view *targeting.RequestView, adType, deviceID string, caps []redisx.CapWindow, isTest bool, res redisx.BidReservation) error {
	if h.redis == nil {
		// Без Redis биддер в degraded-режиме (только dev): bookkeeping невозможен,
		// но и резервов нет — бид уходит как раньше.
		return nil
	}
	rdb := h.redis.Raw()
	dt := deviceTypeName(view.DeviceType)
	browser := targeting.DeviceBrowser(view.UA)
	testFlag := 0
	if isTest {
		testFlag = 1
	}
	businessDate := res.BusinessDate
	if businessDate == "" {
		businessDate = redisx.BusinessDate(time.Now())
	}
	hasTotal, hasLi := 0, 0
	if res.HasTotal {
		hasTotal = 1
	}
	if res.HasLi {
		hasLi = 1
	}
	// bidmeta читает backend в win/imp/click (rtb.py:_find_bid) — формат Python,
	// плюс is_test, чтобы win по тестовому запросу не списывал деньги, и
	// описание резерва (audit M-04/M-06): business_date, advertiser_id, reserve,
	// has_total, has_li — по ним loss/expire/win двигают ровно те ключи, что
	// были зарезервированы. Inventory-измерения (bundle/publisher/tagid/
	// region/city) — audit E-01: win/imp/click/loss наследуют их от бида,
	// иначе в группировках «по площадкам» они попадают в «—».
	meta, _ := json.Marshal(map[string]any{
		"bid_id": bidID, "imp_id": imp.ID,
		"campaign_id": c.CampaignID, "line_item_id": c.LiID,
		"creative_id": c.CreativeID, "supply_partner_id": partner.ID,
		"advertiser_id": c.AdvertiserID,
		"price":         roundPrice(price),
		"reserve":       res.Reserve,
		"has_total":     hasTotal,
		"has_li":        hasLi,
		"device_type":   dt, "geo_country": view.Country, "ad_type": adType,
		"geo_region": view.Region, "geo_city": view.City,
		"domain": view.Domain, "bundle": view.Bundle, "publisher_id": view.PubID, "tagid": imp.TagID,
		"os": view.OS, "browser": browser,
		"is_test":       testFlag,
		"business_date": businessDate,
	})
	if err := rdb.Set(ctx, "bidmeta:"+bidID, meta, time.Hour).Err(); err != nil {
		return fmt.Errorf("set bidmeta: %w", err)
	}

	// Событие «bid» -> Redis Stream -> backend/events_consumer.py -> bid_events +
	// stats_hourly.bids. Расширенные поля (tagid/publisher/bundle/city/region)
	// нужны отчётам «по площадкам» и оптимизатору. Все значения — строки: так
	// их читает XREADGROUP на стороне Python.
	ev := map[string]any{
		"type":              "bid",
		"bid_id":            bidID,
		"imp_id":            imp.ID,
		"campaign_id":       c.CampaignID,
		"line_item_id":      c.LiID,
		"creative_id":       c.CreativeID,
		"supply_partner_id": partner.ID,
		"price":             strconv.FormatFloat(roundPrice(price), 'f', 4, 64),
		"bidfloor":          strconv.FormatFloat(imp.BidFloor, 'f', 4, 64),
		"device_type":       dt,
		"geo_country":       view.Country,
		"geo_region":        view.Region,
		"geo_city":          view.City,
		"ad_type":           adType,
		"domain":            view.Domain,
		"bundle":            view.Bundle,
		"publisher_id":      view.PubID,
		"tagid":             imp.TagID,
		"os":                view.OS,
		"browser":           browser,
		"is_test":           strconv.Itoa(testFlag),
		"ts":                strconv.FormatInt(time.Now().Unix(), 10),
	}
	// Decision log (bid_events.pctr): какой pCTR повлиял на цену — для offline-
	// оценки модели. Пустое поле = базовый биддинг, consumer пишет NULL.
	if pctrUsed > 0 {
		ev["pctr"] = strconv.FormatFloat(pctrUsed, 'f', 6, 64)
	}
	// Событие bid — единственный путь бида в bid_events/stats_hourly; без него
	// win/imp/click повиснут без «родителя», а отчёт win-rate/CPA поедет (R-02).
	if err := h.redis.EmitEvent(ctx, ev); err != nil {
		if h.metrics != nil {
			h.metrics.IncEventEmitError()
		}
		return fmt.Errorf("xadd bid event: %w", err)
	}
	// bidfreq/clidmap — вторичны: их отсутствие лишь ослабляет freq cap/ретаргетинг
	// для одного бида, а не теряет деньги. Логируем, бид не отменяем.
	if deviceID != "" && len(caps) > 0 {
		capsJSON, _ := json.Marshal(caps)
		if err := rdb.Set(ctx, "bidfreq:"+bidID, deviceID+"|"+string(capsJSON), time.Hour).Err(); err != nil {
			slog.Warn("set bidfreq", "bid_id", bidID, "err", err)
		}
	}
	// Retargeting bridge: clid == bid_id после клика. Ключ и TTL — контракт с
	// backend/audiences.py (resolve_clid читает clidmap:{bid_id}, CLIDMAP_TTL 7d):
	// пиксель на лендинге может сработать через дни после клика. Раньше Go писал
	// clid:{bid} на 24h — пиксель его не находил, сегменты с Go-трафика не росли.
	if deviceID != "" {
		if err := rdb.Set(ctx, clidMapPrefix+bidID, deviceID, clidMapTTL).Err(); err != nil {
			slog.Warn("set clidmap", "bid_id", bidID, "err", err)
		}
	}
	return nil
}

// abortReservation возвращает резерв бида, который решили не отдавать SSP
// (bookkeeping не записался). Идёт по тому же Lua, что loss/expired, поэтому
// идемпотентен и переводит bidstate в терминальное 'aborted'. Пустой резерв
// (test=1, Redis nil) — no-op. Ошибку только логируем: если и это не удалось,
// pending:bids подберёт sweeper (expired) — деньги вернутся, пусть и позже.
func (h *Handler) abortReservation(ctx context.Context, res redisx.BidReservation) {
	if h.redis == nil || res.BidID == "" {
		return
	}
	if _, err := h.redis.ReleaseBid(ctx, res, "aborted"); err != nil {
		slog.Error("abort reservation", "bid_id", res.BidID, "err", err)
	}
}

const (
	clidMapPrefix  = "clidmap:"
	clidMapTTL     = 7 * 24 * time.Hour
	audiencePrefix = "aud:" // aud:{segment_id} — SET device_id; наполняет пиксель (audiences.py)
)

// audienceOK — порт check_audiences (backend/audiences.py): устройство должно
// быть хотя бы в одном include-сегменте и ни в одном exclude. Fail-open при
// ошибке Redis (торгуем как без сегментов) — паритет с Python. Без device_id
// include невыполним, exclude — не мешает.
func (h *Handler) audienceOK(ctx context.Context, include, exclude []string, deviceID string) bool {
	if len(include) == 0 && len(exclude) == 0 {
		return true
	}
	if deviceID == "" {
		return len(include) == 0
	}
	if h.redis == nil {
		return true
	}
	pipe := h.redis.Raw().Pipeline()
	cmds := make([]*redis.BoolCmd, 0, len(include)+len(exclude))
	for _, sid := range include {
		cmds = append(cmds, pipe.SIsMember(ctx, audiencePrefix+sid, deviceID))
	}
	for _, sid := range exclude {
		cmds = append(cmds, pipe.SIsMember(ctx, audiencePrefix+sid, deviceID))
	}
	if _, err := pipe.Exec(ctx); err != nil && !errors.Is(err, redis.Nil) {
		return true
	}
	if len(include) > 0 {
		anyIn := false
		for _, c := range cmds[:len(include)] {
			if c.Val() {
				anyIn = true
				break
			}
		}
		if !anyIn {
			return false
		}
	}
	for _, c := range cmds[len(include):] {
		if c.Val() {
			return false
		}
	}
	return true
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
	// reason — OpenRTB Loss Reason Code (${AUCTION_LOSS}); mtw — минимальная
	// цена для выигрыша (${AUCTION_MIN_TO_WIN}). SSP может не подставить макросы —
	// тогда строки остаются как есть, и мы пишем пусто.
	reason := macroOrEmpty(r.URL.Query().Get("reason"))
	mtw := macroOrEmpty(r.URL.Query().Get("mtw"))
	if h.redis != nil {
		ctx := r.Context()
		raw, err := h.redis.Raw().Get(ctx, "bidmeta:"+bidID).Result()
		if err == nil {
			var meta struct {
				CampaignID      string  `json:"campaign_id"`
				LineItemID      string  `json:"line_item_id"`
				CreativeID      string  `json:"creative_id"`
				SupplyPartnerID string  `json:"supply_partner_id"`
				Price           float64 `json:"price"`
				DeviceType      string  `json:"device_type"`
				GeoCountry      string  `json:"geo_country"`
				AdType          string  `json:"ad_type"`
				GeoRegion       string  `json:"geo_region"`
				GeoCity         string  `json:"geo_city"`
				Domain          string  `json:"domain"`
				Bundle          string  `json:"bundle"`
				PublisherID     string  `json:"publisher_id"`
				TagID           string  `json:"tagid"`
				OS              string  `json:"os"`
				Browser         string  `json:"browser"`
				IsTest          int     `json:"is_test"`
			}
			if json.Unmarshal([]byte(raw), &meta) == nil && meta.CampaignID != "" {
				// Audit M-06: терминальное состояние loss и возврат резерва — один
				// атомарный Lua. Повторный lurl, loss после win или после expire
				// ничего не двигают (released=false) и событие не дублируют.
				released := true
				if meta.IsTest == 0 {
					res, ok := redisx.ParseReservation(raw)
					if ok {
						res.BidID = bidID
						var rerr error
						released, rerr = h.redis.ReleaseBid(ctx, res, "loss")
						if rerr != nil {
							// Ничего не помечено: SSP может повторить lurl, sweeper
							// дожмёт expire — резерв не потеряется.
							slog.Warn("release_bid", "err", rerr, "bid", bidID)
							if h.metrics != nil {
								h.metrics.IncGateError()
							}
							w.WriteHeader(http.StatusNoContent)
							return
						}
					}
				} else if first, _ := h.redis.Raw().SetNX(ctx, redisx.BidStateKey(bidID), "loss", redisx.BidStateTTL).Result(); !first {
					released = false
				}
				if !released {
					w.WriteHeader(http.StatusNoContent)
					return
				}
				if h.metrics != nil {
					h.metrics.IncLossRelease()
				}
				if err := h.redis.EmitEvent(ctx, map[string]any{
					"type": "loss", "bid_id": bidID,
					"campaign_id": meta.CampaignID, "line_item_id": meta.LineItemID,
					"creative_id": meta.CreativeID, "supply_partner_id": meta.SupplyPartnerID,
					"price":       strconv.FormatFloat(meta.Price, 'f', 4, 64),
					"loss_reason": reason, "min_to_win": mtw,
					"device_type": meta.DeviceType, "geo_country": meta.GeoCountry,
					"geo_region": meta.GeoRegion, "geo_city": meta.GeoCity,
					"ad_type": meta.AdType, "domain": meta.Domain, "bundle": meta.Bundle,
					"publisher_id": meta.PublisherID, "tagid": meta.TagID,
					"os": meta.OS, "browser": meta.Browser,
					"is_test": strconv.Itoa(meta.IsTest),
					"ts":      strconv.FormatInt(time.Now().Unix(), 10),
				}); err != nil {
					slog.Warn("emit loss event", "err", err)
					if h.metrics != nil {
						h.metrics.IncEventEmitError()
					}
				}
			}
		}
	}
	w.WriteHeader(http.StatusNoContent)
}

// macroOrEmpty — если SSP не подставил макрос (осталось "${...}"), возвращаем "".
func macroOrEmpty(v string) string {
	if strings.HasPrefix(v, "${") {
		return ""
	}
	return v
}

// Imp — POST /rtb/imp (в prod imp идёт на backend /api/rtb/imp).
func (h *Handler) Imp(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusNoContent)
}

// Healthz — GET /healthz: liveness + возраст фида.
// FeedReload — POST /internal/feed/reload: немедленный рефреш фида.
// Требует internalCheckHeader = RTB_API_TOKEN; иначе 403 (или 404, если токен
// не настроен — эндпоинта «как бы нет»).
func (h *Handler) FeedReload(w http.ResponseWriter, r *http.Request) {
	if h.internalToken == "" {
		http.NotFound(w, r)
		return
	}
	if !h.isInternalCheck(r) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	// Audit R-02: возвращаем реальный результат. Backend (ssp_check) по нему
	// понимает, попала ли новая кампания в снапшот, а не получает «ok: true»
	// при упавшем рефреше.
	err := h.store.Refresh(ctx)
	st := h.store.Status()
	w.Header().Set("Content-Type", "application/json")
	body := map[string]any{
		"ok":               err == nil,
		"campaigns":        h.store.Len(),
		"feed_age_seconds": st.Age.Seconds(),
		"stale":            st.Stale,
	}
	if err != nil {
		body["error"] = err.Error()
		w.WriteHeader(http.StatusBadGateway)
	}
	_ = json.NewEncoder(w).Encode(body)
}

// Healthz — GET /healthz. 200 только когда биддер реально способен торговать:
// фид загружен и не старше FEED_MAX_AGE. Иначе 503 — оркестратор/балансировщик
// выводит инстанс из ротации вместо того, чтобы SSP получали no-bid (R-02).
// Redis отражается в теле, но не валит liveness: без него биддер деградирует
// (нет резервов/freq cap), а не ломается.
func (h *Handler) Healthz(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	redisOK := false
	if h.redis != nil && h.redis.Ping(r.Context()) == nil {
		redisOK = true
	}
	st := h.store.Status()
	status := "ok"
	if st.Stale {
		status = "feed_stale"
		if !st.Loaded {
			status = "feed_not_loaded"
		}
		w.WriteHeader(http.StatusServiceUnavailable)
	}
	body := map[string]any{
		"status":           status,
		"campaigns":        st.Candidates,
		"partners":         st.Partners,
		"feed_age_sec":     int(st.Age.Seconds()),
		"feed_max_age_sec": int(st.MaxAge.Seconds()),
		"feed_loaded":      st.Loaded,
		"redis":            redisOK,
	}
	if st.LastError != "" {
		body["feed_error"] = st.LastError
	}
	_ = json.NewEncoder(w).Encode(body)
}

// --- helpers ---

// prefilterDevice — проекция device для предфильтра; nil-device = пустые поля
// (тогда сработает no_device_id, как req.get("device") or {} в Python).
func prefilterDevice(d *Device) prefilter.Device {
	if d == nil {
		return prefilter.Device{}
	}
	pd := prefilter.Device{UA: d.UA, IFA: d.IFA, IP: d.IP, IPv6: d.IPv6}
	if d.Geo != nil {
		pd.Country = d.Geo.Country
	}
	return pd
}

// deviceTypeName — OpenRTB devicetype -> имя измерения (DEVICE_TYPES.get(..., "unknown")).
func deviceTypeName(dt int) string {
	if name := targeting.DeviceTypes[dt]; name != "" {
		return name
	}
	return "unknown"
}

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
		v.Make = strings.ToLower(strings.TrimSpace(d.Make))
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
	v.SChainOK = schainComplete(req.Source)
	return v
}

// schainComplete — source.schain (2.6) или source.ext.schain (2.5) присутствует,
// complete=1 и есть хотя бы одна нода. Нужен для targeting.schain_required.
func schainComplete(src *Source) bool {
	if src == nil {
		return false
	}
	sc := src.SChain
	if sc == nil && len(src.Ext) > 0 {
		var ext struct {
			SChain *SupplyChain `json:"schain"`
		}
		if json.Unmarshal(src.Ext, &ext) == nil {
			sc = ext.SChain
		}
	}
	return sc != nil && sc.Complete == 1 && len(sc.Nodes) > 0
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
		v.VideoPlacement = imp.Video.Plcmt
		if v.VideoPlacement == 0 {
			v.VideoPlacement = imp.Video.Placement
		}
		v.VideoProtocols = imp.Video.Protocols
	}
	// Viewability: доля 0..1 -> проценты; нет метрики = -1.
	v.Viewability = -1
	for _, m := range imp.Metric {
		if strings.EqualFold(m.Type, "viewability") {
			pct := int(m.Value*100 + 0.5)
			if m.Value > 1 { // некоторые SSP шлют уже проценты
				pct = int(m.Value + 0.5)
			}
			if pct > v.Viewability {
				v.Viewability = pct
			}
		}
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

// iabBlocked — есть ли среди категорий креатива запрещённая bcat (точно или
// по tier-1 родителю: bcat "IAB7" покрывает "IAB7-3"; "IAB7-3" не покрывает "IAB7").
func iabBlocked(bcat, creativeCats []string) bool {
	for _, b := range bcat {
		b = strings.ToUpper(strings.TrimSpace(b))
		if b == "" {
			continue
		}
		for _, cc := range creativeCats {
			cc = strings.ToUpper(strings.TrimSpace(cc))
			if cc == b || strings.HasPrefix(cc, b+"-") {
				return true
			}
		}
	}
	return false
}

// battrBlocked — креатив имеет атрибут, запрещённый в imp.banner|video.battr.
func battrBlocked(battr, attrs []int) bool {
	for _, a := range attrs {
		for _, b := range battr {
			if a == b {
				return true
			}
		}
	}
	return false
}

func typeMatches(c *feed.Candidate, adType string, imp *Imp) bool {
	if c.Type != adType {
		return false
	}
	// battr: запрещённые издателем атрибуты креатива (авто-звук, expandable…).
	switch {
	case imp.Banner != nil && len(imp.Banner.BAttr) > 0 && battrBlocked(imp.Banner.BAttr, c.Attrs):
		return false
	case imp.Video != nil && len(imp.Video.BAttr) > 0 && battrBlocked(imp.Video.BAttr, c.Attrs):
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
		DeviceMakes: t.DeviceMakes, SChainRequired: t.SChainRequired,
	}
}

// feedImpTargeting — imp-level часть таргетинга line item.
func feedImpTargeting(t *feed.Targeting) *targeting.ImpTargeting {
	return &targeting.ImpTargeting{
		ViewabilityMin: t.ViewabilityMin, VideoPlacements: t.VideoPlacements, VideoProtocols: t.VideoProtocols,
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
