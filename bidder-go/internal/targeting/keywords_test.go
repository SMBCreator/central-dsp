package targeting

// Слова v2 (ТЗ приложения A и B). Общие векторы читает и Python
// (backend/tests/test_keywords_v2.py): расхождение валит CI с обеих сторон.

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"strings"
	"testing"
)

type kwVector struct {
	Name        string   `json:"name"`
	Mode        string   `json:"mode"`
	Haystack    []string `json:"haystack"`
	Allow       []string `json:"allow"`
	Block       []string `json:"block"`
	ExpectAllow bool     `json:"expect_allow"`
	ExpectBlock bool     `json:"expect_block"`
}

func loadKWVectors(t *testing.T) []kwVector {
	t.Helper()
	raw, err := os.ReadFile("testdata/keyword_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Cases []kwVector `json:"cases"`
	}
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	return f.Cases
}

func srcOf(h []string) KWSources {
	var s KWSources
	copy(s[:], h)
	return s
}

func TestKeywordVectors(t *testing.T) {
	cases := loadKWVectors(t)
	if len(cases) < 100 {
		t.Fatalf("too few keyword vectors: %d", len(cases))
	}
	for _, c := range cases {
		allow, blocked := EvalKeywords(c.Mode, srcOf(c.Haystack), c.Allow, c.Block)
		if allow != c.ExpectAllow || blocked != c.ExpectBlock {
			t.Errorf("%s: allow=%v blocked=%v, want allow=%v blocked=%v", c.Name, allow, blocked, c.ExpectAllow, c.ExpectBlock)
		}
	}
}

func TestNormAndTokenize(t *testing.T) {
	cases := []struct {
		in   string
		want []string
	}{
		{"iPhone-15 Pro", []string{"iphone", "15", "pro"}},
		{"Ёлка, ёж", []string{"елка", "еж"}},
		{"под_черту/слэш.точка%процент@собака", []string{"под", "черту", "слэш", "точка", "процент", "собака"}},
		{"!!!", nil},
		{"", nil},
		{"٣٤", []string{"٣٤"}}, // арабско-индийские цифры (Nd)
	}
	for _, c := range cases {
		got := tokenize(normText(c.in))
		if strings.Join(got, "|") != strings.Join(c.want, "|") {
			t.Errorf("tokenize(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestNormPage(t *testing.T) {
	cases := []struct{ in, want string }{
		{"https://x.ru/%D0%B8%D0%BF", "https://x.ru/ип"},
		{"/a+b", "/a+b"},             // «+» не пробел
		{"/%ZZ%D0%B8", "/%zz%d0%b8"}, // битая последовательность: исходная строка
		{"/%D0%98%D0%9F", "/ип"},     // регистр после декодирования
		{"/%D0", "/\ufffd"},          // обрезанный UTF-8: U+FFFD, не паника
		{"100%", "100%"},             // одиночный % в конце
	}
	for _, c := range cases {
		if got := normPage(c.in); got != c.want {
			t.Errorf("normPage(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestKeywordLimits(t *testing.T) {
	// Слово длиннее 100 символов, фраза из 9 токенов и пустые отбрасываются.
	long := strings.Repeat("а", KWMaxWordRunes+1)
	nine := strings.Repeat("a ", KWMaxPhraseTokens+1)
	exact := strings.Repeat("б", KWMaxWordRunes)
	eight := strings.TrimSpace(strings.Repeat("a ", KWMaxPhraseTokens))
	keys := prepareKwKeys([]string{long, nine, "!!!", "", exact, eight, "ok"})
	if len(keys) != 3 || keys[0].nk != exact || keys[1].nk != strings.Join(strings.Fields(eight), " ") || keys[2].nk != "ok" {
		t.Fatalf("prepareKwKeys = %+v", keys)
	}

	// Список режется до 200 слов.
	many := make([]string, KWMaxWords+50)
	for i := range many {
		many[i] = "w" + strings.Repeat("x", i%5) + string(rune('a'+i%26))
	}
	if got := len(prepareKwKeys(many)); got != KWMaxWords {
		t.Fatalf("keys after cap = %d, want %d", got, KWMaxWords)
	}

	// Источник режется до 2048 символов; слово за границей не находится.
	body := strings.Repeat("я", KWMaxSourceRunes) + " хвост"
	if a, _ := EvalKeywords("substring", KWSources{"", "", "", body}, []string{"хвост"}, nil); a {
		t.Fatal("word after 2048 chars must be cut off")
	}
	edge := strings.Repeat("я", KWMaxSourceRunes-6) + " хвост"
	if a, _ := EvalKeywords("substring", KWSources{"", "", "", edge}, []string{"хвост"}, nil); !a {
		t.Fatal("word inside 2048 chars must be found")
	}

	// В тексте не более 512 токенов (разделители не считаются): 600 токенов
	// в двух источниках (каждый короче 2048 символов).
	tokenList := func(from, to int) string {
		var sb strings.Builder
		for i := from; i < to; i++ {
			sb.WriteString(fmt.Sprintf("t%03d ", i))
		}
		return sb.String()
	}
	src := KWSources{"", tokenList(0, 300), "", tokenList(300, 600)}
	x := buildKWText(src)
	real := 0
	for _, tk := range x.t {
		if tk != kwSep {
			real++
		}
	}
	if real != KWMaxTokens {
		t.Fatalf("tokens = %d, want %d", real, KWMaxTokens)
	}
	if a, _ := EvalKeywords("whole_word", src, []string{"t511"}, nil); !a {
		t.Fatal("token 511 must be matched")
	}
	if a, _ := EvalKeywords("whole_word", src, []string{"t512"}, nil); a {
		t.Fatal("token beyond 512 must not be matched by whole_word")
	}
}

func TestKeywordsNoPanicOnAnyInput(t *testing.T) {
	inputs := []string{
		"", "%", "%%", "%2", "%zz", "%C0%AF", "%ED%A0%80", "\xff\xfe\xfd", "a\x00b", "\x00",
		"ёЁ", "İ", "ΑΣ", "\u0301", "%00", strings.Repeat("%D0", 5000), strings.Repeat("я", 10000),
		string([]byte{0xe0, 0x80}), "𝒜𝒷𝒸", "\u200b\u200d",
	}
	rng := rand.New(rand.NewSource(1))
	for i := 0; i < 500; i++ {
		b := make([]byte, rng.Intn(64))
		for j := range b {
			b[j] = byte(rng.Intn(256))
		}
		inputs = append(inputs, string(b))
	}
	for _, mode := range []string{"substring", "word_start", "whole_word", ""} {
		for _, s := range inputs {
			EvalKeywords(mode, KWSources{s, s, s, s}, []string{s, "a b", "ё"}, []string{s, "%"})
		}
	}
}

// Легаси-путь: без keywords_match слова не подготавливаются и текст v2 не строится.
func TestLegacyPathDoesNotTouchV2(t *testing.T) {
	tg := &Targeting{KeywordsAllow: []string{"ипотек"}}
	tg.PrepareKeywords()
	if tg.kw != nil {
		t.Fatal("legacy targeting must not prepare v2 keywords")
	}
	v := &RequestView{Keywords: KeywordHaystack("", "", "", "Ипотека"), KWSrc: KWSources{"", "", "", "Ипотека"}}
	if reason := MatchReason(tg, v); reason != ReasonNone {
		t.Fatalf("legacy reason = %v", reason)
	}
	if v.kwx != nil {
		t.Fatal("legacy path must not build the v2 request text")
	}
}

// Текст запроса строится лениво и ровно один раз на запрос.
func TestRequestTextBuiltOncePerRequest(t *testing.T) {
	v := &RequestView{KWSrc: KWSources{"", "", "", "Ипотека в Москве"}}
	a := &Targeting{KeywordsAllow: []string{"ипотек"}, KeywordsMatch: "word_start"}
	b := &Targeting{KeywordsAllow: []string{"москв"}, KeywordsMatch: "word_start", KeywordsBlock: []string{"порно"}}
	a.PrepareKeywords()
	b.PrepareKeywords()
	if v.kwx != nil {
		t.Fatal("text must not exist before first v2 candidate")
	}
	if MatchReason(a, v) != ReasonNone {
		t.Fatal("a must match")
	}
	first := v.kwx
	if first == nil {
		t.Fatal("text must be built by first v2 candidate")
	}
	if MatchReason(b, v) != ReasonNone || v.kwx != first {
		t.Fatal("second candidate must reuse the same text")
	}
}

// Без PrepareKeywords (Targeting собран напрямую) результат тот же.
func TestUnpreparedV2TargetingStillMatches(t *testing.T) {
	tg := &Targeting{KeywordsAllow: []string{"ипотек"}, KeywordsMatch: "word_start"}
	v := &RequestView{KWSrc: KWSources{"", "", "", "Ипотека"}}
	if MatchReason(tg, v) != ReasonNone {
		t.Fatal("unprepared v2 targeting must match")
	}
	v2 := &RequestView{KWSrc: KWSources{"", "", "", "Банкет"}}
	if MatchReason(tg, v2) != ReasonKeywords {
		t.Fatal("unprepared v2 targeting must reject non-matching text")
	}
}

func TestUnknownKeywordsMatchValueFallsBackToLegacy(t *testing.T) {
	tg := &Targeting{KeywordsAllow: []string{"елка"}, KeywordsMatch: "garbage"}
	v := &RequestView{Keywords: KeywordHaystack("", "", "", "Ёлка"), KWSrc: KWSources{"", "", "", "Ёлка"}}
	if MatchReason(tg, v) != ReasonKeywords {
		t.Fatal("unknown keywords_match must behave as legacy (no ё normalization)")
	}
}
