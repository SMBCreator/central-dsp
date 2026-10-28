package targeting

import (
	"net/url"
	"strings"
	"unicode"
	"unicode/utf8"
)

// Слова v2 (docs/CONTRACTS.md §1.3, ТЗ приложение A). Включаются только если у
// line item задан keywords_match; без него исполняется легаси-путь (anySubstring
// по Keywords) без единого изменения. Python-эталон — backend/ctx_match.py;
// оба слоя читают общие векторы testdata/keyword_cases.json.

// Лимиты (ТЗ A.4–A.5). Backend отвергает кампании сверх лимитов (422); здесь —
// защитная обрезка на случай данных в обход валидации.
const (
	KWMaxSourceRunes  = 2048 // длина одного источника текста запроса
	KWMaxTokens       = 512  // токенов в тексте запроса (разделители не считаются)
	KWMaxWords        = 200  // слов в одном списке
	KWMaxWordRunes    = 100  // символов в слове
	KWMaxPhraseTokens = 8    // токенов во фразе
)

// KWMode — режим сопоставления разрешающих слов.
type KWMode uint8

const (
	KWLegacy    KWMode = iota // keywords_match не задан: старый алгоритм
	KWSubstring               // "substring": подстрока в N или T
	KWWordStart               // "word_start": токены ключа — префиксы подряд идущих токенов
	KWWholeWord               // "whole_word": токены ключа равны подряд идущим токенам
)

// ParseKWMode разбирает значение keywords_match. Пусто и неизвестные значения
// дают KWLegacy: данные в обход валидации не должны менять поведение.
func ParseKWMode(s string) KWMode {
	switch s {
	case "substring":
		return KWSubstring
	case "word_start":
		return KWWordStart
	case "whole_word":
		return KWWholeWord
	}
	return KWLegacy
}

// kwSep — токен-разделитель между источниками и элементами CSV: не совпадает
// ни с одним ключом (в ключах нет символа \x00).
const kwSep = "\x00"

// KWSources — источники текста запроса в порядке ТЗ A.4: site/app.keywords,
// content.keywords, адрес страницы, content.title.
type KWSources [4]string

const (
	kwSrcKeywords = iota
	kwSrcContentKeywords
	kwSrcPage
	kwSrcTitle
)

// kwText — текст запроса в двух представлениях: N (нормализованная строка) и
// T (токены с границами; tj — они же через пробел).
type kwText struct {
	n  string
	t  []string
	tj string
}

// isWordRune — символ слова: буквы (L*), десятичные цифры (Nd), комбинирующие знаки (M*).
func isWordRune(r rune) bool {
	return unicode.IsLetter(r) || unicode.IsDigit(r) || unicode.IsMark(r)
}

// normText — нижний регистр и ё → е. NFC не выполняется (ТЗ A.2 п.4).
func normText(s string) string {
	return strings.ReplaceAll(strings.ToLower(s), "ё", "е")
}

// normPage — как normText, но перед этим декодирует процентную кодировку.
// PathUnescape не превращает «+» в пробел; при ошибке берётся исходная строка.
func normPage(s string) string {
	if strings.IndexByte(s, '%') >= 0 {
		if u, err := url.PathUnescape(s); err == nil {
			s = u
		}
	}
	return normText(s)
}

// tokenize режет строку на слова по Unicode (не регулярками: \b в Go только ASCII).
func tokenize(s string) []string {
	var out []string
	start := -1
	for i, r := range s {
		if isWordRune(r) {
			if start < 0 {
				start = i
			}
			continue
		}
		if start >= 0 {
			out = append(out, s[start:i])
			start = -1
		}
	}
	if start >= 0 {
		out = append(out, s[start:])
	}
	return out
}

// clipRunes отбрасывает хвост строки сверх max символов.
func clipRunes(s string, max int) string {
	if len(s) <= max {
		return s
	}
	n := 0
	for i := range s {
		if n == max {
			return s[:i]
		}
		n++
	}
	return s
}

// buildKWText строит представления N и T. Пустые источники пропускаются.
func buildKWText(src KWSources) *kwText {
	var n strings.Builder
	var toks []string
	real := 0
	for i, raw := range src {
		if raw == "" {
			continue
		}
		raw = clipRunes(raw, KWMaxSourceRunes)
		var s string
		if i == kwSrcPage {
			s = normPage(raw)
		} else {
			s = normText(raw)
		}
		if s == "" {
			continue
		}
		if n.Len() > 0 {
			n.WriteByte('\n')
		}
		n.WriteString(s)
		if real >= KWMaxTokens {
			continue
		}
		groups := []string{s}
		if i == kwSrcKeywords || i == kwSrcContentKeywords {
			groups = strings.Split(s, ",")
		}
		for _, g := range groups {
			gt := tokenize(g)
			if len(gt) == 0 {
				continue
			}
			if real >= KWMaxTokens {
				break
			}
			if len(toks) > 0 {
				toks = append(toks, kwSep)
			}
			for _, tk := range gt {
				if real >= KWMaxTokens {
					break
				}
				toks = append(toks, tk)
				real++
			}
		}
	}
	return &kwText{n: n.String(), t: toks, tj: strings.Join(toks, " ")}
}

func (x *kwText) empty() bool { return x.n == "" }

// kwKey — подготовленное слово: nk (токены через пробел) и сами токены.
type kwKey struct {
	nk   string
	toks []string
}

// prepareKwKeys — подготовка списка при загрузке фида (ТЗ A.5): normText →
// токены; слова без токенов, длиннее лимита или с лишним числом токенов
// отбрасываются; список режется до KWMaxWords.
func prepareKwKeys(list []string) []kwKey {
	if len(list) > KWMaxWords {
		list = list[:KWMaxWords]
	}
	var out []kwKey
	for _, w := range list {
		if utf8.RuneCountInString(w) > KWMaxWordRunes {
			continue
		}
		toks := tokenize(normText(w))
		if len(toks) == 0 || len(toks) > KWMaxPhraseTokens {
			continue
		}
		out = append(out, kwKey{nk: strings.Join(toks, " "), toks: toks})
	}
	return out
}

// kwPrepared — слова line item, подготовленные при загрузке фида.
type kwPrepared struct {
	mode  KWMode
	allow []kwKey
	block []kwKey
}

func newKWPrepared(mode KWMode, allow, block []string) *kwPrepared {
	return &kwPrepared{mode: mode, allow: prepareKwKeys(allow), block: prepareKwKeys(block)}
}

// PreparedKeywords — слова line item, подготовленные при загрузке фида.
type PreparedKeywords = kwPrepared

// NewPreparedKeywords готовит слова v2 при загрузке фида (один раз на line
// item). Без keywords_match возвращает nil: легаси-путь работает с сырыми списками.
func NewPreparedKeywords(mode string, allow, block []string) *PreparedKeywords {
	m := ParseKWMode(mode)
	if m == KWLegacy {
		return nil
	}
	return newKWPrepared(m, allow, block)
}

// UsePreparedKeywords подключает слова, подготовленные при загрузке фида.
func (t *Targeting) UsePreparedKeywords(p *PreparedKeywords) { t.kw = p }

// PrepareKeywords готовит слова v2 из самого Targeting (для кода и тестов,
// собирающих Targeting напрямую).
func (t *Targeting) PrepareKeywords() {
	t.kw = NewPreparedKeywords(t.KeywordsMatch, t.KeywordsAllow, t.KeywordsBlock)
}

// kwState — подготовленные слова v2 или nil для легаси-пути. Для Targeting,
// собранных в обход PrepareKeywords (тесты), готовит на лету без кеширования.
func (t *Targeting) kwState() *kwPrepared {
	if t.kw != nil {
		return t.kw
	}
	if mode := ParseKWMode(t.KeywordsMatch); mode != KWLegacy {
		return newKWPrepared(mode, t.KeywordsAllow, t.KeywordsBlock)
	}
	return nil
}

// substringIn — широкое совпадение: nk в N или в T (T не пересекает границы
// источников и элементов CSV: между ними стоит kwSep).
func (k kwKey) substringIn(x *kwText) bool {
	return strings.Contains(x.n, k.nk) || strings.Contains(x.tj, k.nk)
}

// windowIn — токены ключа подряд идущие в T, каждый сопоставлен через eq.
func (k kwKey) windowIn(x *kwText, eq func(key, text string) bool) bool {
	kt, tt := k.toks, x.t
	for i := 0; i+len(kt) <= len(tt); i++ {
		ok := true
		for j, w := range kt {
			if !eq(w, tt[i+j]) {
				ok = false
				break
			}
		}
		if ok {
			return true
		}
	}
	return false
}

func tokenEqual(key, text string) bool  { return key == text }
func tokenPrefix(key, text string) bool { return strings.HasPrefix(text, key) }

// allowMatch: хотя бы один разрешающий ключ совпал по режиму. Пустой текст — false.
func (p *kwPrepared) allowMatch(x *kwText) bool {
	if x.empty() {
		return false
	}
	for _, k := range p.allow {
		var ok bool
		switch p.mode {
		case KWWordStart:
			ok = k.windowIn(x, tokenPrefix)
		case KWWholeWord:
			ok = k.windowIn(x, tokenEqual)
		default:
			ok = k.substringIn(x)
		}
		if ok {
			return true
		}
	}
	return false
}

// blockMatch: стоп-слова всегда широко, независимо от режима. Пустой текст — false.
func (p *kwPrepared) blockMatch(x *kwText) bool {
	if x.empty() {
		return false
	}
	for _, k := range p.block {
		if k.substringIn(x) {
			return true
		}
	}
	return false
}

// kwTextOnce — текст запроса для слов v2: строится лениво один раз на запрос.
func (r *RequestView) kwTextOnce() *kwText {
	if r.kwx == nil {
		r.kwx = buildKWText(r.KWSrc)
	}
	return r.kwx
}

// EvalKeywords — чистая функция для тестов паритета и векторов: allowOK
// (true, если разрешающих слов после очистки нет) и blocked для заданных источников.
// mode — значение keywords_match, пусто = легаси.
func EvalKeywords(mode string, src KWSources, allow, block []string) (allowOK, blocked bool) {
	m := ParseKWMode(mode)
	if m == KWLegacy {
		hay := KeywordHaystack(src[:]...)
		allowOK = len(allow) == 0 || anySubstring(allow, hay)
		blocked = len(block) > 0 && anySubstring(block, hay)
		return allowOK, blocked
	}
	p := newKWPrepared(m, allow, block)
	x := buildKWText(src)
	allowOK = len(p.allow) == 0 || p.allowMatch(x)
	blocked = len(p.block) > 0 && p.blockMatch(x)
	return allowOK, blocked
}
