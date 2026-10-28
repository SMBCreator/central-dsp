package targeting

import (
	"testing"
	"time"
)

// Бенчмарки Match: «до/после» для изменений hot path (ТЗ: рост не более ~5% на
// кампаниях без новых ключей). Запуск: go test ./internal/targeting -bench Match -benchmem.

func benchView() *RequestView {
	return &RequestView{
		Country: "KZ", Region: "almaty", City: "almaty", DeviceType: 1, OS: "android",
		UA: "mozilla/5.0 (linux; android 13) chrome/120", Language: "ru", ConnType: 2,
		SiteCats: []string{"IAB1-2", "IAB2"}, Domain: "news.example.ru", Env: "site",
		Keywords: KeywordHaystack("ипотека, новостройки", "квартиры", "https://news.example.ru/realty", "Купить квартиру в Москве"),
		Now:      time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC),
	}
}

func BenchmarkMatchTypical(b *testing.B) {
	v := benchView()
	t := &Targeting{
		GeoCountries: []string{"KZ", "RU"}, DeviceTypes: []string{"mobile"}, OS: []string{"android", "ios"},
		IABCategories: []string{"IAB1", "IAB2", "IAB4"}, IABBlock: []string{"IAB25", "IAB26"},
		DomainBlocklist: []string{"bad.example"}, Hours: []int{9, 10, 11, 12, 13},
		KeywordsBlock: []string{"порно"},
	}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if !Match(t, v) {
			b.Fatal("must match")
		}
	}
}

func BenchmarkMatchRejectGeo(b *testing.B) {
	v := benchView()
	t := &Targeting{GeoCountries: []string{"US"}, IABCategories: []string{"IAB1"}}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if Match(t, v) {
			b.Fatal("must not match")
		}
	}
}

func BenchmarkMatchEmptyTargeting(b *testing.B) {
	v := benchView()
	t := &Targeting{}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if !Match(t, v) {
			b.Fatal("must match")
		}
	}
}
