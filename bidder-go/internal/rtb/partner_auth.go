package rtb

// Shared-secret токен SSP-партнёра. Контракт — backend/ssp_tokens.py:
// в фиде приходит hex(sha256(token)) (supply_partners.auth_token_hash,
// миграция 032), партнёр шлёт открытый токен в заголовке X-CentralDSP-Token.
// Сверка — sha256 от заголовка и сравнение в константное время.

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"net/http"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// TokenHeader — каноническое имя заголовка (совпадает с Python и UI).
const TokenHeader = "X-CentralDSP-Token"

// legacyTokenHeader принимался ранними сборками Go-биддера; оставлен для
// партнёров, подключённых до унификации заголовка.
const legacyTokenHeader = "X-SSP-Token"

// partnerTokenOK возвращает true, если токен партнёру не выдан (проверка не
// требуется) или sha256(заголовок) совпадает с хэшем из фида.
func partnerTokenOK(p *feed.Partner, r *http.Request) bool {
	if p.AuthTokenHash == "" {
		return true
	}
	given := r.Header.Get(TokenHeader)
	if given == "" {
		given = r.Header.Get(legacyTokenHeader)
	}
	if given == "" {
		return false
	}
	sum := sha256.Sum256([]byte(given))
	got := hex.EncodeToString(sum[:])
	return subtle.ConstantTimeCompare([]byte(got), []byte(p.AuthTokenHash)) == 1
}
