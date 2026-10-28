package rtb

// Макросы ${CDSP_*} в ссылке перехода и пикселях креатива (docs/CONTRACTS.md,
// «Макросы в ссылках»). Шаблоны разобраны при загрузке фида
// (feed.Candidate.PrepareMacros); здесь — контекст ставки и развёртка.
//
// Контекст собирается из сырого запроса, а не только из RequestView: во view
// город и регион приведены к нижнему регистру для таргетинга, а в ссылку
// должны попасть значения «как передала SSP» (Алматы, а не алматы).

import (
	"math/rand/v2"
	"strconv"
	"strings"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/macros"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/targeting"
)

// connectionName — OpenRTB device.connectiontype -> wifi | cellular | ethernet | unknown.
func connectionName(t int) string {
	switch t {
	case 1:
		return "ethernet"
	case 2:
		return "wifi"
	case 3, 4, 5, 6, 7:
		return "cellular"
	}
	return "unknown"
}

// buildMacroContext собирает значения каталога для одной ставки. Без вызовов
// Redis и сети; IP, идентификаторы устройства, координаты, название SSP и цена
// клиринга сюда не попадают (решение M3).
func buildMacroContext(req *BidRequest, imp *Imp, view *targeting.RequestView, c *feed.Candidate, partner *feed.Partner, bidID string) *macros.Context {
	ctx := &macros.Context{}
	ctx.Set(macros.BidID, bidID)
	ctx.Set(macros.AuctionID, req.ID)
	ctx.Set(macros.ImpID, imp.ID)
	ctx.Set(macros.CampaignID, c.CampaignID)
	ctx.Set(macros.GroupID, c.LiID)
	ctx.Set(macros.CreativeID, c.CreativeID)

	siteID := ""
	if req.Site != nil {
		siteID = req.Site.ID
	}
	if siteID == "" && req.App != nil {
		siteID = req.App.ID
	}
	ctx.Set(macros.SiteID, siteID)
	ctx.Set(macros.PublisherID, view.PubID)
	ctx.Set(macros.PlacementID, imp.TagID)
	ctx.Set(macros.Domain, view.Domain)
	ctx.Set(macros.Bundle, view.Bundle)
	ctx.Set(macros.SSPID, partner.ID)

	ctx.Set(macros.Country, targeting.NormalizeCountry(view.Country))
	if d := req.Device; d != nil {
		if d.Geo != nil {
			ctx.Set(macros.Region, strings.TrimSpace(d.Geo.Region))
			ctx.Set(macros.City, strings.TrimSpace(d.Geo.City))
		}
		ctx.Set(macros.DeviceModel, strings.TrimSpace(d.Model))
		ctx.Set(macros.Carrier, strings.TrimSpace(d.Carrier))
	}

	ctx.Set(macros.DeviceType, deviceTypeName(view.DeviceType))
	ctx.Set(macros.OS, view.OS)
	ctx.Set(macros.OSVersion, view.OSV)
	ctx.Set(macros.Browser, targeting.DeviceBrowser(view.UA))
	ctx.Set(macros.DeviceMake, view.Make)
	ctx.Set(macros.Connection, connectionName(view.ConnType))
	ctx.Set(macros.Language, view.Language)

	ctx.Set(macros.AdFormat, c.Type)
	w, h := 0, 0
	if c.Width != nil {
		w = *c.Width
	}
	if c.Height != nil {
		h = *c.Height
	}
	if w > 0 {
		ctx.Set(macros.Width, strconv.Itoa(w))
	}
	if h > 0 {
		ctx.Set(macros.Height, strconv.Itoa(h))
	}
	if w > 0 && h > 0 {
		ctx.Set(macros.Size, strconv.Itoa(w)+"x"+strconv.Itoa(h))
	}

	ctx.Set(macros.Timestamp, strconv.FormatInt(view.Now.Unix(), 10))
	ctx.Set(macros.Cachebuster, strconv.FormatUint(uint64(rand.Uint32N(100_000_000)), 10))
	return ctx
}

// expandChecked разворачивает шаблон и проверяет итог теми же правилами, что и
// URL до ставки (safeURL, 2048 символов). ok=false — строку использовать нельзя;
// tooLong отличает превышение длины от прочих нарушений.
func expandChecked(tpl *macros.Template, ctx *macros.Context) (out string, ok, tooLong bool) {
	out = tpl.Expand(ctx)
	if len(out) > maxAdmURLLen {
		return "", false, true
	}
	if !safeURL(out) {
		return "", false, false
	}
	return out, true, false
}

// expandPixel возвращает URL пикселя для разметки: развёрнутый шаблон либо
// raw, если макросов нет. Не прошедший проверку пиксель не вставляется ("") —
// ставка при этом идёт (метрика bidder_macro_dropped_total).
func (h *Handler) expandPixel(tpl *macros.Template, raw string, ctx *macros.Context, field int) string {
	if tpl == nil {
		return raw
	}
	out, ok, tooLong := expandChecked(tpl, ctx)
	if h.metrics != nil {
		h.metrics.IncMacroExpanded(field)
		if !ok {
			h.metrics.IncMacroDropped(field, tooLong)
		}
	}
	return out
}

// expandCreativeURLs разворачивает URL креатива с макросами: пиксель показа,
// клик-пиксель и ссылку перехода. clickLink — "" если в ссылке перехода нет
// макросов или итог непригоден: тогда клик-редирект берёт click_url из БД.
func (h *Handler) expandCreativeURLs(c *feed.Candidate, ctx *macros.Context) (impURL, clickTrackerURL, clickLink string) {
	impURL = h.expandPixel(c.ImpTpl, c.ImpTrackerURL, ctx, metrics.MacroFieldImp)
	clickTrackerURL = h.expandPixel(c.ClickTrackerTpl, c.ClickTrackerURL, ctx, metrics.MacroFieldClickTracker)
	if c.ClickTpl != nil {
		if out, ok, _ := expandChecked(c.ClickTpl, ctx); ok {
			clickLink = out
			if h.metrics != nil {
				h.metrics.IncMacroExpanded(metrics.MacroFieldClick)
			}
		}
	}
	return impURL, clickTrackerURL, clickLink
}
