package rtb

// Shared-secret токен SSP-партнёра. Контракт — backend/ssp_tokens.py:
// в фиде приходит hex(sha256(token)) (supply_partners.auth_token_hash,
// миграция 032), партнёр шлёт открытый токен в заголовке X-CentralDSP-Token.
// Сверка — sha256 от заголовка и сравнение в константное время.
//
// Audit S-06:
//   - партнёр без хэша НЕ авторизован (раньше пустой хэш = «проверка не
//     требуется», и revoke открывал endpoint);
//   - в окне ротации (token_prev_hash до token_prev_valid_until, миграция 039)
//     принимается и предыдущий токен;
//   - X-Forwarded-For / X-Real-IP учитываются только если RemoteAddr входит
//     в TRUSTED_PROXIES; иначе клиентом считается сам RemoteAddr;
//   - невалидный ip_whitelist -> fail-closed (feed.Partner.WhitelistInvalid).

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// TokenHeader — каноническое имя заголовка (совпадает с Python и UI).
const TokenHeader = "X-CentralDSP-Token"

// legacyTokenHeader принимался ранними сборками Go-биддера; оставлен для
// партнёров, подключённых до унификации заголовка.
const legacyTokenHeader = "X-SSP-Token"

// tokenVerdict — результат сверки токена; различаем «хэша нет» и «токен не
// совпал», чтобы метрика показывала отозванных/невыпущенных отдельно.
type tokenVerdict int

const (
	tokenOK tokenVerdict = iota
	tokenNoHash
	tokenMismatch
)

// checkPartnerToken сверяет заголовок с текущим хэшем партнёра, а в окне
// ротации — и с предыдущим. Пустой текущий хэш = tokenNoHash всегда.
func checkPartnerToken(p *feed.Partner, r *http.Request, now time.Time) tokenVerdict {
	if p.AuthTokenHash == "" {
		return tokenNoHash
	}
	given := r.Header.Get(TokenHeader)
	if given == "" {
		given = r.Header.Get(legacyTokenHeader)
	}
	if given == "" {
		return tokenMismatch
	}
	got := sha256Hex(given)
	if subtle.ConstantTimeCompare([]byte(got), []byte(p.AuthTokenHash)) == 1 {
		return tokenOK
	}
	if p.TokenPrevHash != "" && p.TokenPrevValidUntil != nil && now.Before(*p.TokenPrevValidUntil) &&
		subtle.ConstantTimeCompare([]byte(got), []byte(p.TokenPrevHash)) == 1 {
		return tokenOK
	}
	return tokenMismatch
}

// partnerTokenOK — true, если токен принят (текущий или предыдущий в окне ротации).
func partnerTokenOK(p *feed.Partner, r *http.Request) bool {
	return checkPartnerToken(p, r, time.Now()) == tokenOK
}

// sha256Hex — hex(sha256(s)); формат идентичен backend/ssp_tokens.py::hash_token.
func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// ParseTrustedProxies разбирает TRUSTED_PROXIES: список IP/CIDR через запятую
// (например `127.0.0.1,::1,172.16.0.0/12`). Только с этих адресов биддер верит
// X-Forwarded-For / X-Real-IP. Пустая строка = никому не верим (без прокси
// клиентом всегда считается RemoteAddr).
func ParseTrustedProxies(s string) ([]*net.IPNet, error) {
	var out []*net.IPNet
	for _, it := range strings.Split(s, ",") {
		it = strings.TrimSpace(it)
		if it == "" {
			continue
		}
		if _, n, err := net.ParseCIDR(it); err == nil {
			out = append(out, n)
			continue
		}
		ip := net.ParseIP(it)
		if ip == nil {
			return nil, fmt.Errorf("TRUSTED_PROXIES: invalid IP/CIDR %q", it)
		}
		bits := 32
		if ip.To4() == nil {
			bits = 128
		}
		out = append(out, &net.IPNet{IP: ip, Mask: net.CIDRMask(bits, bits)})
	}
	return out, nil
}

// SetTrustedProxies задаёт сети, с которых принимаются proxy-заголовки.
func (h *Handler) SetTrustedProxies(nets []*net.IPNet) { h.trustedProxies = nets }

func (h *Handler) fromTrustedProxy(remote net.IP) bool {
	if remote == nil {
		return false
	}
	for _, n := range h.trustedProxies {
		if n.Contains(remote) {
			return true
		}
	}
	return false
}

// clientIP — IP клиента для IP-whitelist. RemoteAddr — источник истины; XFF /
// X-Real-IP читаются только от доверенного прокси (Caddy на той же машине).
// Из XFF берётся первый элемент: Caddy добавляет свой адрес в конец, а
// подделать начало цепочки может только сам клиент — но его запрос без
// доверенного RemoteAddr сюда не попадёт.
func (h *Handler) clientIP(r *http.Request) net.IP {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	remote := net.ParseIP(strings.TrimSpace(host))
	if !h.fromTrustedProxy(remote) {
		return remote
	}
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
	return remote
}

// partnerIPOK — IP-whitelist партнёра (supply_partners.ip_whitelist, миграция
// 030). Пустой список = принимать откуда угодно. Невалидный список (fail-closed,
// см. feed.ParseIPWhitelist) — партнёр не биддится.
func (h *Handler) partnerIPOK(p *feed.Partner, r *http.Request) bool {
	if p.WhitelistInvalid {
		return false
	}
	if len(p.IPNets) == 0 {
		return true
	}
	ip := h.clientIP(r)
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
