package targeting

// Сопоставление IAB-категорий таргетинга с категориями запроса.
// Python-эталон: backend/ctx_match.py (общие векторы — testdata/match_cases.json).

import "strings"

// NormalizeIABCode приводит код категории к каноническому виду: без внешних
// пробелов, в верхнем регистре; у кодов IAB «IAB7_3» подчёркивание заменяется
// дефисом («IAB7-3»). Коды других таксономий (числовые «1», «1-2» и т.п.)
// остаются как есть, кроме регистра и пробелов, и просто не совпадут с IAB.
func NormalizeIABCode(s string) string {
	if iabCodeIsCanonical(s) {
		return s
	}
	s = strings.ToUpper(strings.TrimSpace(s))
	if strings.HasPrefix(s, "IAB") && strings.IndexByte(s, '_') >= 0 {
		s = strings.ReplaceAll(s, "_", "-")
	}
	return s
}

// iabCodeIsCanonical — быстрый путь без аллокаций: код уже в верхнем ASCII-регистре,
// без пробелов/управляющих символов и подчёркиваний.
func iabCodeIsCanonical(s string) bool {
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c <= ' ' || c >= 0x80 || c == '_' || (c >= 'a' && c <= 'z') {
			return false
		}
	}
	return true
}

// IABMatch — совпал ли код таргетинга want с кодом запроса have:
//   - коды равны (без учёта регистра);
//   - want — tier-1 код без дефиса («IAB7»), а have — его подкатегория («IAB7-3»).
//
// Подкатегория в таргетинге («IAB7-3») родителя запроса («IAB7») не покрывает.
func IABMatch(want, have string) bool {
	return iabMatchNorm(NormalizeIABCode(want), NormalizeIABCode(have))
}

// iabMatchNorm — IABMatch для уже нормализованных кодов.
func iabMatchNorm(w, h string) bool {
	if w == "" || h == "" {
		return false
	}
	if w == h {
		return true
	}
	return strings.IndexByte(w, '-') < 0 && len(h) > len(w) && h[len(w)] == '-' && h[:len(w)] == w
}

// IABIntersects — есть ли пара (want, have), для которой IABMatch истинен.
func IABIntersects(wanted, have []string) bool {
	if len(have) == 0 {
		return false
	}
	for _, w := range wanted {
		nw := NormalizeIABCode(w)
		if nw == "" {
			continue
		}
		for _, h := range have {
			if iabMatchNorm(nw, NormalizeIABCode(h)) {
				return true
			}
		}
	}
	return false
}

// CollectCats — объединение списков категорий запроса: коды нормализованы
// (NormalizeIABCode), пустые отброшены, дубликаты схлопнуты с сохранением
// порядка первого появления. Нет ни одного кода — nil.
//
// Типичный запрос несёт один короткий список уже канонических кодов: тогда он
// возвращается как есть, без копирования (результат только читается).
func CollectCats(lists ...[]string) []string {
	total, nonEmpty, last := 0, 0, 0
	for i, l := range lists {
		if len(l) > 0 {
			total += len(l)
			nonEmpty++
			last = i
		}
	}
	if total == 0 {
		return nil
	}
	if nonEmpty == 1 && catsAreCanonicalUnique(lists[last]) {
		return lists[last]
	}
	out := make([]string, 0, total)
	var seen map[string]struct{}
	if total > 32 {
		seen = make(map[string]struct{}, total)
	}
	for _, l := range lists {
		for _, raw := range l {
			code := NormalizeIABCode(raw)
			if code == "" {
				continue
			}
			if seen != nil {
				if _, dup := seen[code]; dup {
					continue
				}
				seen[code] = struct{}{}
			} else if contains(out, code) {
				continue
			}
			out = append(out, code)
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

// catsAreCanonicalUnique — короткий список (до 8 кодов), где каждый код уже
// нормализован, непуст и не повторяется: CollectCats вернёт его без изменений.
func catsAreCanonicalUnique(l []string) bool {
	if len(l) > 8 {
		return false
	}
	for i, c := range l {
		if c == "" || !iabCodeIsCanonical(c) {
			return false
		}
		for _, prev := range l[:i] {
			if prev == c {
				return false
			}
		}
	}
	return true
}
