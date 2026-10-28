package rtb

import (
	"context"
	"testing"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/domcat"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/targeting"
)

func domcatHandler(entries map[string][]string, l *domcat.Learner) *Handler {
	m := &domcat.Map{}
	m.Set(&domcat.Payload{Domains: entries})
	h := &Handler{metrics: metrics.New()}
	h.SetDomainCats(m, l)
	return h
}

type recWriter struct{ ch chan string }

func (r recWriter) Write(_ context.Context, d string, _ []string) error { r.ch <- d; return nil }

func TestApplyDomainCats_FromMapWhenRequestHasNone(t *testing.T) {
	h := domcatHandler(map[string][]string{"news.ru": {"IAB12"}}, nil)
	req := &BidRequest{Site: &Site{Domain: "WWW.News.ru"}}
	v := buildRequestView(req)
	h.applyDomainCats(v)
	if v.CatSource != "domain_map" || len(v.SiteCats) != 1 || v.SiteCats[0] != "IAB12" {
		t.Fatalf("view=%+v", v)
	}
	// «определённые» категории: iab_allow_unknown уже не пропускает как неизвестные
	tg := &targeting.Targeting{IABCategories: []string{"IAB1"}, IABAllowUnknown: true}
	if targeting.IABIntersects(tg.IABCategories, v.SiteCats) {
		t.Fatal("IAB12 must not match IAB1")
	}
	if len(v.SiteCats) == 0 {
		t.Fatal("categories from the map must count as known")
	}
}

func TestApplyDomainCats_RequestCategoriesWinAndMapIgnored(t *testing.T) {
	h := domcatHandler(map[string][]string{"news.ru": {"IAB12"}}, nil)
	req := &BidRequest{Site: &Site{Domain: "news.ru", Cat: []string{"IAB3"}}}
	v := buildRequestView(req)
	h.applyDomainCats(v)
	if v.CatSource != "request" || len(v.SiteCats) != 1 || v.SiteCats[0] != "IAB3" {
		t.Fatalf("view=%+v", v)
	}
}

func TestApplyDomainCats_UnknownDomainEmptyOrNilMap(t *testing.T) {
	h := domcatHandler(map[string][]string{"news.ru": {"IAB12"}}, nil)
	v := buildRequestView(&BidRequest{Site: &Site{Domain: "other.ru"}})
	h.applyDomainCats(v)
	if v.CatSource != "" || len(v.SiteCats) != 0 {
		t.Fatalf("unknown domain must stay without categories: %+v", v)
	}
	empty := domcatHandler(nil, nil)
	v = buildRequestView(&BidRequest{Site: &Site{Domain: "news.ru"}})
	empty.applyDomainCats(v)
	if len(v.SiteCats) != 0 {
		t.Fatal("empty map must not break the request")
	}
	off := &Handler{}
	v = buildRequestView(&BidRequest{Site: &Site{Domain: "news.ru"}})
	off.applyDomainCats(v)
	if v.CatSource != "" {
		t.Fatal("feature off: view unchanged")
	}
}

func TestApplyDomainCats_LearnsOnlyFromRequestCategories(t *testing.T) {
	w := recWriter{ch: make(chan string, 4)}
	h := domcatHandler(map[string][]string{"mapped.ru": {"IAB1"}}, domcat.NewLearner(w, time.Hour, 100))

	h.applyDomainCats(buildRequestView(&BidRequest{Site: &Site{Domain: "mapped.ru"}})) // из карты — не учим
	h.applyDomainCats(buildRequestView(&BidRequest{Site: &Site{Domain: "bare.ru"}}))   // категорий нет — не учим
	h.applyDomainCats(buildRequestView(&BidRequest{App: &App{Bundle: "com.x.app", Cat: []string{"IAB1"}}}))
	h.applyDomainCats(buildRequestView(&BidRequest{Site: &Site{Domain: "www.Learn.ru", Cat: []string{"IAB1"}}}))
	select {
	case d := <-w.ch:
		if d != "learn.ru" {
			t.Fatalf("learned %q, want learn.ru", d)
		}
	case <-time.After(time.Second):
		t.Fatal("expected one learning write")
	}
	select {
	case d := <-w.ch:
		t.Fatalf("unexpected extra write for %q", d)
	case <-time.After(100 * time.Millisecond):
	}
}
