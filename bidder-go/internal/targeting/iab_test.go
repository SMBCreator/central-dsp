package targeting

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

// matchCases — общие векторы с Python-эталоном (backend/tests/test_ctx_match.py).
type matchCases struct {
	IABNormalize [][2]string `json:"iab_normalize"`
	IABMatch     [][]any     `json:"iab_match"`
}

func loadMatchCases(t *testing.T) matchCases {
	t.Helper()
	raw, err := os.ReadFile("testdata/match_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var mc matchCases
	if err := json.Unmarshal(raw, &mc); err != nil {
		t.Fatal(err)
	}
	return mc
}

func TestNormalizeIABCodeVectors(t *testing.T) {
	for _, c := range loadMatchCases(t).IABNormalize {
		if got := NormalizeIABCode(c[0]); got != c[1] {
			t.Errorf("NormalizeIABCode(%q) = %q, want %q", c[0], got, c[1])
		}
	}
}

func TestIABMatchVectors(t *testing.T) {
	cases := loadMatchCases(t).IABMatch
	if len(cases) < 15 {
		t.Fatalf("too few shared vectors: %d", len(cases))
	}
	for _, c := range cases {
		want, have, exp := c[0].(string), c[1].(string), c[2].(bool)
		if got := IABMatch(want, have); got != exp {
			t.Errorf("IABMatch(%q, %q) = %v, want %v", want, have, got, exp)
		}
	}
}

func TestNormalizeIABCodeFastPathDoesNotAllocate(t *testing.T) {
	if n := testing.AllocsPerRun(100, func() { _ = NormalizeIABCode("IAB12-3") }); n != 0 {
		t.Fatalf("canonical code must not allocate, got %v allocs", n)
	}
}

func TestCollectCats(t *testing.T) {
	cases := []struct {
		name  string
		lists [][]string
		want  []string
	}{
		{"нет списков", nil, nil},
		{"пустые списки", [][]string{nil, {}}, nil},
		{"только пустые коды", [][]string{{"", "  ", "\t"}}, nil},
		{"одно поле", [][]string{{"iab1"}}, []string{"IAB1"}},
		{"несколько полей, порядок первого появления", [][]string{{"IAB2"}, {"IAB1", "IAB2"}, {"IAB3"}}, []string{"IAB2", "IAB1", "IAB3"}},
		{"дубликаты после нормализации", [][]string{{"iab1", " IAB1 ", "IAB1"}}, []string{"IAB1"}},
		{"подчёркивание", [][]string{{"IAB1_2", "IAB1-2"}}, []string{"IAB1-2"}},
		{"один канонический список без копирования", [][]string{{"IAB1", "IAB2-1"}}, []string{"IAB1", "IAB2-1"}},
		{"один список с дублем", [][]string{{"IAB1", "IAB1"}}, []string{"IAB1"}},
		{"один список с пустым кодом", [][]string{{"IAB1", ""}}, []string{"IAB1"}},
		{"один список длиннее быстрого пути", [][]string{{"IAB1", "IAB2", "IAB3", "IAB4", "IAB5", "IAB6", "IAB7", "IAB8", "IAB9", "IAB1"}},
			[]string{"IAB1", "IAB2", "IAB3", "IAB4", "IAB5", "IAB6", "IAB7", "IAB8", "IAB9"}},
		{"пустой список между непустыми", [][]string{{"IAB1"}, nil, {"IAB2"}}, []string{"IAB1", "IAB2"}},
	}
	for _, c := range cases {
		if got := CollectCats(c.lists...); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: CollectCats = %#v, want %#v", c.name, got, c.want)
		}
	}
}

// Много кодов: путь с map не должен деградировать до O(n²) и обязан схлопывать дубликаты.
func TestCollectCatsLargeInput(t *testing.T) {
	big := make([]string, 0, 20000)
	for i := 0; i < 10000; i++ {
		big = append(big, "IAB"+strings.Repeat("1", i%50+1))
		big = append(big, "iab"+strings.Repeat("1", i%50+1)) // тот же код в другом регистре
	}
	start := time.Now()
	got := CollectCats(big)
	if len(got) != 50 {
		t.Fatalf("want 50 unique codes, got %d", len(got))
	}
	if d := time.Since(start); d > time.Second {
		t.Fatalf("CollectCats too slow on 20k codes: %v", d)
	}
}

func TestIABMatchLongAndGarbageValues(t *testing.T) {
	long := strings.Repeat("A", 1<<16)
	if IABMatch(long, "IAB1") || IABMatch("IAB1", long) || !IABMatch(long, long) {
		t.Fatal("very long values must compare without panic and only equal themselves")
	}
	for _, s := range []string{"\x00", "\xff\xfe", "IAB\u0000", "  \u00a0 ", "ІАВ1"} { // кириллические І/А/В
		_ = NormalizeIABCode(s)
		if IABMatch("IAB1", s) {
			t.Errorf("garbage %q must not match IAB1", s)
		}
	}
}

func TestMatchReasonIAB(t *testing.T) {
	v := baseView()
	v.SiteCats = []string{"IAB1-2"}
	if r := MatchReason(&Targeting{IABCategories: []string{"IAB1"}}, v); r != ReasonNone {
		t.Fatalf("parent target must cover subcategory request, got %q", r)
	}
	if r := MatchReason(&Targeting{IABCategories: []string{"IAB9"}}, v); r != ReasonInventoryIAB {
		t.Fatalf("want inventory_iab, got %q", r)
	}
	if r := MatchReason(&Targeting{IABBlock: []string{"IAB1"}}, v); r != ReasonInventoryIAB {
		t.Fatalf("iab_block by parent must reject with inventory_iab, got %q", r)
	}
	v.SiteCats = []string{"iab1"} // RequestView, собранный не через CollectCats, тоже терпим к регистру
	if r := MatchReason(&Targeting{IABCategories: []string{"IAB1"}}, v); r != ReasonNone {
		t.Fatalf("unnormalized view categories must still match, got %q", r)
	}
}

// Match и MatchReason обязаны давать один и тот же bool на любом наборе.
func TestMatchEqualsMatchReasonNone(t *testing.T) {
	views := []*RequestView{baseView(), func() *RequestView {
		v := baseView()
		v.SiteCats, v.Env, v.Bundle, v.Carrier, v.COPPA, v.GDPR, v.DNT = []string{"IAB1-2"}, "app", "com.a", "mts", true, true, true
		return v
	}()}
	targetings := []*Targeting{
		nil, {}, {GeoCountries: []string{"KZ"}}, {DeviceTypes: []string{"ctv"}}, {OS: []string{"ios"}},
		{IABCategories: []string{"IAB1"}}, {IABCategories: []string{"IAB9"}}, {IABBlock: []string{"IAB1"}},
		{DomainBlocklist: []string{"news.example.ru"}}, {DomainAllowlist: []string{"x.ru"}},
		{Genders: []string{"M"}}, {Hours: []int{99}}, {TrafficTypes: []string{"app"}}, {Carriers: []string{"tele2"}},
		{AppBundlesAllow: []string{"com.b"}}, {PublisherAllow: []string{"p"}}, {KeywordsAllow: []string{"авто"}},
		{KeywordsBlock: []string{"ипотека"}}, {IPRanges: []string{"10."}}, {BlockCOPPA: true}, {BlockGDPR: true},
		{SkipDNT: true}, {DeviceMakes: []string{"apple"}}, {SChainRequired: true}, {KeywordsAllow: []string{"ипотека"}},
	}
	for i, tg := range targetings {
		for j, v := range views {
			r := MatchReason(tg, v)
			if got, want := Match(tg, v), r == ReasonNone; got != want {
				t.Errorf("targeting #%d view #%d: Match=%v, MatchReason=%q", i, j, got, r)
			}
			if r >= ReasonCount {
				t.Errorf("targeting #%d view #%d: reason %d out of range", i, j, r)
			}
		}
	}
}

func TestReasonNames(t *testing.T) {
	want := []string{"geo", "device", "inventory_iab", "inventory_domain", "inventory_other",
		"audience", "time", "keywords", "context", "regulatory", "other"}
	for i, name := range want {
		if got := Reason(i + 1).String(); got != name {
			t.Errorf("Reason(%d).String() = %q, want %q", i+1, got, name)
		}
	}
	if ReasonNone.String() != "" || ReasonCount.String() != "" {
		t.Error("ReasonNone and out-of-range reasons must have an empty label")
	}
	if int(ReasonCount)-1 != len(want) {
		t.Errorf("ReasonCount-1 = %d, want %d", int(ReasonCount)-1, len(want))
	}
}

// Каждая группа проверок отдаёт свою причину.
func TestMatchReasonGroups(t *testing.T) {
	v := baseView()
	v.Env, v.COPPA, v.GDPR, v.DNT = "site", true, true, true
	cases := []struct {
		name string
		tg   Targeting
		want Reason
	}{
		{"geo", Targeting{GeoCountries: []string{"KZ"}}, ReasonGeo},
		{"device", Targeting{DeviceTypes: []string{"ctv"}}, ReasonDevice},
		{"os", Targeting{OS: []string{"ios"}}, ReasonDevice},
		{"iab", Targeting{IABCategories: []string{"IAB1"}}, ReasonInventoryIAB},
		{"domain", Targeting{DomainBlocklist: []string{"news.example.ru"}}, ReasonInventoryDomain},
		{"traffic type", Targeting{TrafficTypes: []string{"app"}}, ReasonInventoryOther},
		{"schain", Targeting{SChainRequired: true}, ReasonInventoryOther},
		{"gender", Targeting{Genders: []string{"M"}}, ReasonAudience},
		{"hours", Targeting{Hours: []int{99}}, ReasonTime},
		{"keywords", Targeting{KeywordsAllow: []string{"авто"}}, ReasonKeywords},
		{"coppa", Targeting{BlockCOPPA: true}, ReasonRegulatory},
		{"gdpr", Targeting{BlockGDPR: true}, ReasonRegulatory},
		{"dnt", Targeting{SkipDNT: true}, ReasonRegulatory},
		{"pass", Targeting{}, ReasonNone},
	}
	for _, c := range cases {
		if got := MatchReason(&c.tg, v); got != c.want {
			t.Errorf("%s: reason = %q, want %q", c.name, got, c.want)
		}
	}
	if MatchReason(nil, v) != ReasonNone {
		t.Error("nil targeting must pass")
	}
}
