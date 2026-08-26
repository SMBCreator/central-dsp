// Package freq: multi-window frequency caps (до 3 окон на кампанию).
//
// Спека — паритет с backend/cache.py bump_frequency_multi/check_frequency_cap:
// один атомарный Lua-скрипт проверяет ВСЕ окна и инкрементит их вместе,
// иначе между окнами возможна гонка. Legacy fallback: одно окно 24ч.
// Fail-open при недоступном Redis для check (как Python), fail-safe для bump.
package freq

// Cap — одно окно.
type Cap struct {
	WindowSec int `json:"window_sec"`
	MaxImps   int `json:"max_imps"`
}

// TODO(impl): Check(ctx, deviceID, campaignID, caps []Cap) bool
//             Bump(ctx, deviceID, campaignID, caps []Cap)
// Ключи: freq:{campaign_id}:{device_id}:{window_sec}, TTL = window_sec.
