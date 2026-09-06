// Package config — fail-fast проверки окружения биддера на старте.
//
// Зеркало backend/startup_checks.py (audit S-04): в production процесс не
// поднимается с пустыми, короткими или плейсхолдерными секретами. Биддер с
// «change_me» в EVENT_TOKEN_SECRET подписывает win/imp/click-токены, которые
// подделает кто угодно; без RTB_API_TOKEN он не получит фид и будет молча
// отдавать no-bid. Вне production — только предупреждения.
package config

import (
	"fmt"
	"os"
	"strings"
)

// MinSecretLen — как MIN_SECRET_LEN в backend.
const MinSecretLen = 32

// RequiredSecret — переменная, без которой production небезопасен.
type RequiredSecret struct {
	Name     string
	Fallback string // альтернативная переменная (EVENT_TOKEN_SECRET <- SESSION_SECRET)
	Purpose  string
}

// Required — секреты биддера. Порядок = порядок в сообщении об ошибке.
var Required = []RequiredSecret{
	{Name: "EVENT_TOKEN_SECRET", Fallback: "SESSION_SECRET", Purpose: "подписывает win/imp/click event-токены (nurl/lurl)"},
	{Name: "RTB_API_TOKEN", Fallback: "INTERNAL_FEED_TOKEN", Purpose: "Bearer для фида /rtb-feed/* и internal-ручек"},
}

var placeholderPrefixes = []string{"change_me", "changeme", "change-me", "replace_me", "todo", "xxx"}

var weakValues = map[string]bool{
	"dev": true, "test": true, "secret": true, "password": true, "postgres": true,
	"admin": true, "root": true, "123456": true, "centraldsp": true,
	"centraldsp-dev-event-secret": true, // dev-fallback из tokens.go/tokens.py
}

// Env — источник переменных; в тестах подменяется map-ом.
type Env func(key string) string

// OSEnv читает os.Getenv.
func OSEnv(key string) string { return os.Getenv(key) }

// MapEnv — Env поверх map (для тестов).
func MapEnv(m map[string]string) Env {
	return func(k string) string { return m[k] }
}

// IsProduction — как startup_checks.is_production.
func IsProduction(env Env) bool {
	switch strings.ToLower(strings.TrimSpace(env("APP_ENV"))) {
	case "production", "prod":
		return true
	}
	return false
}

// SecretProblem возвращает описание нарушения или "" если значение пригодно.
func SecretProblem(name, value string) string {
	v := strings.TrimSpace(value)
	if v == "" {
		return name + " не задан"
	}
	low := strings.ToLower(v)
	for _, p := range placeholderPrefixes {
		if strings.HasPrefix(low, p) {
			return name + " — плейсхолдер (" + v[:min(len(v), 12)] + "…)"
		}
	}
	if weakValues[low] {
		return name + " — слабое/дефолтное значение"
	}
	if len(v) < MinSecretLen {
		return fmt.Sprintf("%s короче %d символов", name, MinSecretLen)
	}
	return ""
}

// CollectProblems — список нарушений. Пусто = конфигурация пригодна для production.
func CollectProblems(env Env) []string {
	var problems []string
	for _, r := range Required {
		val := env(r.Name)
		if strings.TrimSpace(val) == "" && r.Fallback != "" {
			val = env(r.Fallback)
		}
		if p := SecretProblem(r.Name, val); p != "" {
			problems = append(problems, p+" — "+r.Purpose)
		}
	}
	// Redis в production обязан быть задан явно: дефолт localhost означает
	// «биддер смотрит не в тот Redis, что backend» => двойной учёт бюджета (M-03).
	if IsProduction(env) && strings.TrimSpace(env("REDIS_URL")) == "" {
		problems = append(problems, "REDIS_URL не задан — биддер и backend обязаны использовать один Redis (audit M-03)")
	}
	return problems
}

// Assert — вызывать на старте. В production при нарушениях возвращает ошибку
// с полным перечнем (вызывающий делает os.Exit(1)); иначе — nil и список
// предупреждений для лога.
func Assert(env Env) (warnings []string, err error) {
	problems := CollectProblems(env)
	if len(problems) == 0 {
		return nil, nil
	}
	if IsProduction(env) {
		return nil, fmt.Errorf("небезопасная конфигурация для APP_ENV=production:\n  - %s",
			strings.Join(problems, "\n  - "))
	}
	return problems, nil
}
