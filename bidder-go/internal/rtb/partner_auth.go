package rtb

// Shared-secret токен SSP-партнёра. Контракт — backend/ssp_tokens.py:
// в фиде приходит hex(sha256(token)) (supply_partners.auth_token_hash,
// миграция 032), партнёр шлёт открытый токен в заголовке X-CentralDSP-Token.
// Сверка — sha256 от заголовка и сравнение в константное время.

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"net"
	"net/http"
	"strings"

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
	got := sha256Hex(given)
	return subtle.ConstantTimeCompare([]byte(got), []byte(p.AuthTokenHash)) == 1
}

// sha256Hex — hex(sha256(s)); формат идентичен backend/ssp_tokens.py::hash_token.
func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// partnerIPOK — IP-whitelist партнёра (supply_partners.ip_whitelist, миграция
// 030). Пустой список = принимать откуда угодно. Паритет с rtb.py
// (ssp_check.ip_allowed). Биддер стоит за Caddy, поэтому клиентский IP берём
// из первого элемента X-Forwarded-For; без прокси — RemoteAddr.
func partnerIPOK(p *feed.Partner, r *http.Request) bool {
	if len(p.IPNets) == 0 {
		return true
	}
	ip := clientIP(r)
	if ip == nil {
		return false
	}
	for _, n := range p.IPNets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

func clientIP(r *http.Request) net.IP {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		first := strings.TrimSpace(strings.Split(xff, ",")[0])
		if ip := net.ParseIP(first); ip != nil {
			return ip
		}
	}
	if xr := r.Header.Get("X-Real-IP"); xr != "" {
		if ip := net.ParseIP(strings.TrimSpace(xr)); ip != nil {
			return ip
		}
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	return net.ParseIP(host)
}
