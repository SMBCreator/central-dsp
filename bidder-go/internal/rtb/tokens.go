package rtb

// HMAC-signed event tokens — порт 1:1 backend/tokens.py.
// Формат: v1.<bid_id>.<exp_unix>.<sig>, sig = hex(HMAC-SHA256(secret, "v1|bid_id|exp"))[:32].
// Секрет обязан совпадать с Python (EVENT_TOKEN_SECRET | SESSION_SECRET), иначе
// win/imp/click-нотисы, подписанные Go-биддером, не пройдут верификацию в backend.

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

const tokenVersion = "v1"

// DefaultTokenTTL — как DEFAULT_TTL_SECONDS в Python (6 часов).
const DefaultTokenTTL = 6 * time.Hour

func tokenSecret() ([]byte, error) {
	s := os.Getenv("EVENT_TOKEN_SECRET")
	if s == "" {
		s = os.Getenv("SESSION_SECRET")
	}
	if s == "" {
		env := strings.ToLower(os.Getenv("APP_ENV"))
		if env == "production" || env == "prod" {
			return nil, fmt.Errorf("EVENT_TOKEN_SECRET (or SESSION_SECRET) must be set in production")
		}
		s = "centraldsp-dev-event-secret" // dev-only fallback, идентичен Python
	}
	return []byte(s), nil
}

func tokenSig(secret []byte, bidID string, exp int64) string {
	mac := hmac.New(sha256.New, secret)
	fmt.Fprintf(mac, "%s|%s|%d", tokenVersion, bidID, exp)
	return hex.EncodeToString(mac.Sum(nil))[:32]
}

// SignEventToken подписывает bid_id — порт sign_event_token.
func SignEventToken(bidID string, ttl time.Duration) (string, error) {
	secret, err := tokenSecret()
	if err != nil {
		return "", err
	}
	exp := time.Now().Unix() + int64(ttl.Seconds())
	return fmt.Sprintf("%s.%s.%d.%s", tokenVersion, bidID, exp, tokenSig(secret, bidID, exp)), nil
}

// VerifyEventToken возвращает bid_id при валидном токене — порт verify_event_token.
func VerifyEventToken(token string) string {
	if !strings.HasPrefix(token, tokenVersion+".") {
		if os.Getenv("RTB_ALLOW_UNSIGNED_EVENTS") == "1" {
			return token
		}
		return ""
	}
	parts := strings.Split(token, ".")
	if len(parts) != 4 {
		return ""
	}
	bidID, expS, sig := parts[1], parts[2], parts[3]
	exp, err := strconv.ParseInt(expS, 10, 64)
	if err != nil || exp < time.Now().Unix() {
		return ""
	}
	secret, err := tokenSecret()
	if err != nil {
		return ""
	}
	if !hmac.Equal([]byte(sig), []byte(tokenSig(secret, bidID, exp))) {
		return ""
	}
	return bidID
}
