package macros

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"unicode/utf8"
)

type vector struct {
	Name     string            `json:"name"`
	Template string            `json:"template"`
	Values   map[string]string `json:"values"`
	Want     string            `json:"want"`
}

func ctxFrom(t testing.TB, values map[string]string) *Context {
	t.Helper()
	ctx := &Context{}
	for name, v := range values {
		k, ok := KeyOf(name)
		if !ok {
			t.Fatalf("в векторе неизвестный ключ каталога %q", name)
		}
		ctx.Set(k, v)
	}
	return ctx
}

// expandRaw — как вызывающий код: nil-шаблон значит «строка без макросов».
func expandRaw(raw string, ctx *Context) string {
	if tpl := Parse(raw); tpl != nil {
		return tpl.Expand(ctx)
	}
	return raw
}

// Общие векторы читает и Python (backend/tests/test_url_macros.py).
func TestSharedVectors(t *testing.T) {
	b, err := os.ReadFile("testdata/macro_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []vector
	if err := json.Unmarshal(b, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 60 {
		t.Fatalf("векторов %d, нужно не меньше 60", len(cases))
	}
	for _, c := range cases {
		if got := expandRaw(c.Template, ctxFrom(t, c.Values)); got != c.Want {
			t.Errorf("%s:\n шаблон %q\n  ждали %q\n  имеем %q", c.Name, c.Template, c.Want, got)
		}
	}
}

// Каталог: имена и макросы в Go-таблице совпадают с backend/url_macros.json
// (тот же приём, что TestAdSizesParity).
func TestCatalogParity(t *testing.T) {
	b, err := os.ReadFile("../../../backend/url_macros.json")
	if err != nil {
		t.Fatal(err)
	}
	var cat []struct {
		Key   string `json:"key"`
		Macro string `json:"macro"`
	}
	if err := json.Unmarshal(b, &cat); err != nil {
		t.Fatal(err)
	}
	if len(cat) != len(Names) {
		t.Fatalf("в каталоге %d макросов, в Go-таблице %d", len(cat), len(Names))
	}
	for i, e := range cat {
		if Names[i] != e.Key {
			t.Errorf("позиция %d: Go %q, каталог %q", i, Names[i], e.Key)
		}
		if e.Macro != "${CDSP_"+e.Key+"}" {
			t.Errorf("макрос %q не соответствует ключу %q", e.Macro, e.Key)
		}
		if k, ok := KeyOf(e.Key); !ok || int(k) != i {
			t.Errorf("KeyOf(%q) = %d,%v, ждали %d", e.Key, k, ok, i)
		}
	}
}

// Решение M3: персональные и коммерческие данные в каталог не входят.
func TestForbiddenMacrosAbsent(t *testing.T) {
	for _, n := range Names {
		for _, bad := range []string{"IP", "IFA", "IDFA", "GAID", "USER_ID", "BUYERUID", "LAT", "LON", "GEO_LAT", "GEO_LON", "SSP_NAME", "PRICE", "CLEARING_PRICE", "AUCTION_PRICE"} {
			if n == bad {
				t.Errorf("в каталоге запрещённый макрос %s", n)
			}
		}
	}
}

func TestParseNilWithoutKnownMacros(t *testing.T) {
	for _, s := range []string{"", "https://a.example/?q=1", "${AUCTION_PRICE}", "${CDSP_COUNTY}", "${CDSP_COUNTRY", "${cdsp_country}"} {
		if Parse(s) != nil {
			t.Errorf("Parse(%q) должен вернуть nil", s)
		}
	}
	if Parse("https://a.example/?c=${CDSP_COUNTRY}") == nil {
		t.Error("известный макрос должен давать шаблон")
	}
}

func TestNoPanicOnGarbage(t *testing.T) {
	garbage := []string{
		"\xff\xfe${CDSP_COUNTRY}\xc3", "${CDSP_", "${CDSP_${CDSP_${CDSP_", "}}}}${CDSP_COUNTRY}{{{{",
		strings.Repeat("${CDSP_COUNTRY}", 2000), strings.Repeat("a", 10000) + "${CDSP_CITY}", strings.Repeat("${CDSP_", 3000),
		"%", "%%${CDSP_COUNTRY}%", "${CDSP_COUNTRY}%zz",
	}
	ctx := &Context{}
	ctx.Set(Country, "K\xffZ")
	ctx.Set(City, strings.Repeat("я", 5000))
	for _, s := range garbage {
		out := expandRaw(s, ctx)
		_ = utf8.ValidString(out)
	}
}

func TestExpandBrokenUTF8InValueIsEscapedPerByte(t *testing.T) {
	ctx := &Context{}
	ctx.Set(City, "a\xffb")
	if got := expandRaw("x=${CDSP_CITY}", ctx); got != "x=a%FFb" {
		t.Errorf("got %q", got)
	}
}

// Быстрый путь: строка без макросов не аллоцирует ни при разборе, ни при выборе.
func TestNoMacroFastPathAllocs(t *testing.T) {
	const raw = "https://l.example/page?utm_source=x&utm_campaign=y"
	ctx := &Context{}
	n := testing.AllocsPerRun(100, func() {
		if tpl := Parse(raw); tpl != nil {
			_ = tpl.Expand(ctx)
		}
	})
	if n != 0 {
		t.Errorf("быстрый путь аллоцирует %.0f раз, ждали 0", n)
	}
}

func BenchmarkParseNoMacro(b *testing.B) {
	const raw = "https://l.example/page?utm_source=x&utm_campaign=y"
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = Parse(raw)
	}
}

func BenchmarkExpand(b *testing.B) {
	tpl := Parse("https://t.example/i?c=${CDSP_COUNTRY}&s=${CDSP_SITE_ID}&city=${CDSP_CITY}&cb=${CDSP_CACHEBUSTER}")
	ctx := &Context{}
	ctx.Set(Country, "KZ")
	ctx.Set(SiteID, "12345")
	ctx.Set(City, "Алматы")
	ctx.Set(Cachebuster, "48201733")
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = tpl.Expand(ctx)
	}
}
