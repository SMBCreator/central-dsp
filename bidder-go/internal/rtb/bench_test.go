package rtb

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/domcat"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// Бенчмарки hot path: «до/после» для изменений buildRequestView и фильтров
// кандидатов (ТЗ: рост не более ~5% на кампаниях без новых ключей).
// Запуск: go test ./internal/rtb -bench 'BuildRequestView|BidManyCandidates' -benchmem.

const benchViewRequest = `{"id":"b1","imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":1}],` +
	`"site":{"domain":"News.Example.ru","page":"https://news.example.ru/realty/%D0%B8%D0%BF%D0%BE%D1%82%D0%B5%D0%BA%D0%B0",` +
	`"cat":["IAB1","IAB1-2"],"keywords":"ипотека, новостройки, квартиры",` +
	`"publisher":{"id":"pub-1"},"content":{"title":"Купить квартиру в Москве","keywords":"недвижимость"}},` +
	`"device":{"ua":"Mozilla/5.0 (Linux; Android 13) Chrome/120","os":"android","devicetype":1,"ifa":"dev-1",` +
	`"geo":{"country":"KAZ","region":"Almaty","city":"Almaty"}},"user":{"yob":1990,"gender":"M"}}`

func BenchmarkBuildRequestView(b *testing.B) {
	var req BidRequest
	if err := json.Unmarshal([]byte(benchViewRequest), &req); err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if v := buildRequestView(&req); v == nil {
			b.Fatal("nil view")
		}
	}
}

// BenchmarkBidManyCandidates — полный путь Bid на снапшоте из 500 кандидатов с
// разным таргетингом (большинство отсекается), без Redis.
func BenchmarkBidManyCandidates(b *testing.B) {
	snap := testSnapshot()
	w, h := 300, 250
	base := snap.Candidates[0]
	cands := make([]feed.Candidate, 0, 500)
	for i := 0; i < 500; i++ {
		c := base
		c.LiID = strconv.Itoa(1000 + i)
		c.CampaignID = strconv.Itoa(2000 + i)
		c.CreativeID = strconv.Itoa(3000 + i)
		c.Width, c.Height = &w, &h
		switch i % 5 {
		case 0:
			c.T = &feed.Targeting{GeoCountries: []string{"RU"}}
		case 1:
			c.T = &feed.Targeting{IABCategories: []string{"IAB9"}}
		case 2:
			c.T = &feed.Targeting{DeviceTypes: []string{"desktop"}, OS: []string{"windows"}}
		case 3:
			c.T = &feed.Targeting{KeywordsAllow: []string{"автомобиль"}}
		default:
			c.T = &feed.Targeting{GeoCountries: []string{"KZ"}, IABCategories: []string{"IAB1"}}
		}
		cands = append(cands, c)
	}
	snap.Candidates = cands
	handler := newTestHandler(snap)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		rec := httptest.NewRecorder()
		handler.Bid(rec, bidRequest("key1", benchViewRequest))
		if rec.Code != http.StatusOK && rec.Code != http.StatusNoContent {
			b.Fatalf("unexpected status %d: %s", rec.Code, strings.TrimSpace(rec.Body.String()))
		}
	}
}

// BenchmarkBuildRequestViewDomcat — тот же запрос + applyDomainCats: с
// категориями от SSP (путь обучения: гейт «раз в час» отсекает повторы) и без
// них (поиск в справочнике). Сравнивать с BenchmarkBuildRequestView.
func BenchmarkBuildRequestViewDomcat(b *testing.B) {
	for _, tc := range []struct {
		name string
		mut  func(*BidRequest)
	}{
		{"request_cats", func(*BidRequest) {}},
		{"domain_map", func(r *BidRequest) { r.Site.Cat = nil }},
	} {
		b.Run(tc.name, func(b *testing.B) {
			var req BidRequest
			if err := json.Unmarshal([]byte(benchViewRequest), &req); err != nil {
				b.Fatal(err)
			}
			tc.mut(&req)
			h := domcatHandler(map[string][]string{"news.example.ru": {"IAB1"}},
				domcat.NewLearner(recWriter{ch: make(chan string, 8)}, time.Hour, 1000))
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				v := buildRequestView(&req)
				h.applyDomainCats(v)
			}
		})
	}
}
