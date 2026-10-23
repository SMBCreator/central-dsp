// Package geo: GEO v2 — иерархический гео-таргетинг по place_id (docs/GEO.md).
//
// Порт backend/geo_norm.py и backend/geo_v2.py (эталон). Паритет закреплён
// общими кейсами testdata/fixtures.json, которые читают и Go-, и Python-тесты.
package geo

import (
	"strings"
	"unicode"
	"unicode/utf8"
)

// isSeparator — пунктуация и разделители, которые превращаются в пробел
// (geo_norm._SPACE_CHARS), плюс пробельные символы.
func isSeparator(r rune) bool {
	switch r {
	case '"', '\'', '`', '’', '‘', '«', '»', '“', '”', '(', ')', '.', ',', ';', ':',
		'-', '–', '—', '_', '/', '\\', '\u00a0':
		return true
	}
	return unicode.IsSpace(r)
}

// lowerRune — как str.lower() в Python для гео-названий: "İ" -> "i" (Python
// дал бы "i" + combining dot), "ё" -> "е", конечная сигма "ς" -> "σ" (Python
// выбирает форму по позиции, Go — нет; сводим к одной).
func lowerRune(r rune) rune {
	switch r {
	case 'İ':
		return 'i'
	case 'Ё', 'ё':
		return 'е'
	case 'ς':
		return 'σ'
	}
	if r < utf8.RuneSelf {
		if 'A' <= r && r <= 'Z' {
			return r + ('a' - 'A')
		}
		return r
	}
	if l := unicode.ToLower(r); l != 'ς' {
		return l
	}
	return 'σ'
}

var prefixes = []string{"г ", "город ", "city of "}

// expandToken — сокращения целыми словами (geo_norm._TOKEN_EXPAND).
func expandToken(buf []byte, start int) []byte {
	switch string(buf[start:]) {
	case "обл":
		return append(buf[:start], "область"...)
	case "респ":
		return append(buf[:start], "республика"...)
	}
	return buf
}

// Normalize — порт geo_norm.geo_normalize за один проход по строке.
// Справочник хранит синонимы уже нормализованными, поэтому в hot path
// достаточно нормализовать значение запроса и сделать map lookup.
func Normalize(value string) string {
	if value == "" {
		return ""
	}
	buf := make([]byte, 0, len(value)+8)
	tokStart, pendingSpace := 0, false
	for _, r := range value {
		r = lowerRune(r)
		if isSeparator(r) {
			if len(buf) > tokStart {
				buf = expandToken(buf, tokStart)
				pendingSpace = true
			}
			continue
		}
		if pendingSpace {
			buf = append(buf, ' ')
			tokStart, pendingSpace = len(buf), false
		}
		buf = utf8.AppendRune(buf, r)
	}
	if len(buf) > tokStart {
		buf = expandToken(buf, tokStart)
	}
	s := string(buf)
	for _, p := range prefixes {
		if strings.HasPrefix(s, p) && len(s) > len(p) {
			return s[len(p):]
		}
	}
	return s
}
