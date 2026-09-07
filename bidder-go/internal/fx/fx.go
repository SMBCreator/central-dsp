// Package fx — порт договора валюты из backend/fx.py (audit R-03, B8 «Валюта»).
//
// Внутри биддера все деньги — RUB: цена кандидата, floor после конверсии,
// резерв, бюджеты. Наружу (BidResponse.cur / bid.price) — валюта расчётов
// партнёра, если SSP её принимает, иначе RUB, иначе no-bid.
//
// Один effective_rate на (валюта, наценка владельца кампании) применяется ко
// всему биду: floor -> RUB (×), цена RUB -> валюта ответа (÷), а Python win()
// переводит ${AUCTION_PRICE} обратно в RUB тем же курсом из bidmeta.fx_rate.
// Наценка — буфер волатильности: ставка в валюте ниже, floor и списание в
// рублях выше. Нет курса для нужной валюты — кандидат не торгуется, а не
// биддит «как будто в рублях».
package fx

import "strings"

const (
	RUB = "RUB"
	// DefaultMarkupPercent = routers.settings.DEFAULTS["fx_markup_percent"]
	// = backend/fx.py DEFAULT_MARKUP_PERCENT.
	DefaultMarkupPercent = 3.0
)

// ChooseCurrency — валюта ответа. req.cur пуст -> валюта партнёра. Иначе
// валюта партнёра, если SSP её принимает; RUB, если принимает рубли; иначе
// "" (no-bid). Зеркало fx.choose_currency.
func ChooseCurrency(reqCur []string, partnerCurrency string) string {
	pc := norm(partnerCurrency)
	allowed := normList(reqCur)
	if len(allowed) == 0 {
		return pc
	}
	if contains(allowed, pc) {
		return pc
	}
	if contains(allowed, RUB) {
		return RUB
	}
	return ""
}

// FloorCurrency — валюта imp.bidfloor: bidfloorcur -> req.cur[0] -> валюта
// партнёра. Зеркало fx.floor_currency.
func FloorCurrency(bidFloorCur string, reqCur []string, partnerCurrency string) string {
	if bidFloorCur != "" {
		return strings.ToUpper(bidFloorCur)
	}
	if allowed := normList(reqCur); len(allowed) > 0 {
		return allowed[0]
	}
	return norm(partnerCurrency)
}

// EffectiveRate — RUB за единицу cur с наценкой владельца кампании.
// RUB -> 1. Нет курса или курс <= 0 -> (0, false): бид в этой валюте
// невозможен. markup == nil -> DefaultMarkupPercent (как None в Python).
// Зеркало fx.effective_rate.
func EffectiveRate(cur string, rates map[string]float64, markup *float64) (float64, bool) {
	cur = norm(cur)
	if cur == RUB {
		return 1.0, true
	}
	raw, ok := rates[cur]
	if !ok || raw <= 0 {
		return 0, false
	}
	m := DefaultMarkupPercent
	if markup != nil {
		m = *markup
	}
	return raw * (1 + m/100), true
}

func norm(cur string) string {
	if cur == "" {
		return RUB
	}
	return strings.ToUpper(cur)
}

func normList(in []string) []string {
	out := make([]string, 0, len(in))
	for _, c := range in {
		if c != "" {
			out = append(out, strings.ToUpper(c))
		}
	}
	return out
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}
