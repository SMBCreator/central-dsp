// Package shading: bid shading факторы (обучаются офлайн в Python cron,
// биддер только ЧИТАЕТ фактор из Redis: shading:{campaign_id}:{partner_id}).
//
// Инварианты (паритет с backend/shading.py + фикс GAP-005):
//   - shading применяется ТОЛЬКО при at=1 (first price) и bid_shading=true
//   - цена никогда не опускается ниже floor*1.01
//   - фактор в [MinFactor, 1.0]; отсутствие ключа = 1.0 (no-op)
package shading

const MinFactor = 0.60 // нижняя граница фактора, как FACTOR_MIN в Python

// ShouldShade — гейт применения shading (порт should_shade из backend/shading.py).
func ShouldShade(bidShadingEnabled bool, auctionType int) bool {
	return bidShadingEnabled && auctionType == 1
}

// TODO(impl): GetFactor(ctx, campaignID, partnerID) float64 — чтение из Redis
// с локальным кэшем на ~10s (фактор пересчитывается кроном раз в N минут).
