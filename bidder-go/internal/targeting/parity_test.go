package targeting

// Паритет с backend/routers/rtb.py::_match_targeting (хвост) и
// _match_imp_targeting. Каждый кейс — одна ветка Python-референса.

import (
	"testing"
	"time"
)

func baseView() *RequestView {
	return &RequestView{
		Country: "RU", Env: "site", Domain: "news.example.ru", Now: time.Now(),
		Keywords: KeywordHaystack("ипотека, новостройки", "квартиры", "https://news.example.ru/realty", "Купить квартиру в Москве"),
	}
}

func TestMatchTrafficTypes(t *testing.T) {
	tg := &Targeting{TrafficTypes: []string{"app"}}
	if Match(tg, baseView()) {
		t.Fatal("site traffic must not match traffic_types=[app]")
	}
	v := baseView()
	v.Env = "app"
	if !Match(tg, v) {
		t.Fatal("app traffic must match traffic_types=[app]")
	}
}

func TestMatchCarriers(t *testing.T) {
	tg := &Targeting{Carriers: []string{"MTS", "Beeline"}}
	v := baseView()
	if Match(tg, v) {
		t.Fatal("empty device.carrier must fail when carriers are set")
	}
	v.Carrier = "mts russia"
	if !Match(tg, v) {
		t.Fatal("substring match (targeting ⊂ carrier) expected")
	}
	v.Carrier = "bee"
	if !Match(tg, v) {
		t.Fatal("substring match (carrier ⊂ targeting) expected")
	}
	v.Carrier = "tele2"
	if Match(tg, v) {
		t.Fatal("tele2 must not match")
	}
}

func TestMatchOSVersionMin(t *testing.T) {
	cases := []struct {
		osv, min string
		want     bool
	}{
		{"17.2", "16.4", true},
		{"16.4", "16.4", true},
		{"16.3", "16.4", false},
		{"17", "16.4", true},     // (17,) > (16,4)
		{"16", "16.4", false},    // (16,) < (16,4) — семантика кортежей Python
		{"16.4.1", "16.4", true}, // берутся только два компонента
		{"", "16.4", false},      // osv не передан
		{"abc", "16.4", false},   // ValueError -> False
		{"17.0", "x", false},     // мусор в таргетинге -> False
	}
	for _, c := range cases {
		v := baseView()
		v.OSV = c.osv
		got := Match(&Targeting{OSVersionMin: c.min}, v)
		if got != c.want {
			t.Errorf("osv=%q min=%q: got %v want %v", c.osv, c.min, got, c.want)
		}
	}
}

func TestMatchAppBundles(t *testing.T) {
	v := baseView()
	v.Env, v.Bundle = "app", "com.example.game"
	if Match(&Targeting{AppBundlesBlock: []string{"COM.EXAMPLE.GAME"}}, v) {
		t.Fatal("blocked bundle (case-insensitive) must fail")
	}
	if !Match(&Targeting{AppBundlesAllow: []string{"com.example.game"}}, v) {
		t.Fatal("allowed bundle must pass")
	}
	if Match(&Targeting{AppBundlesAllow: []string{"com.other"}}, v) {
		t.Fatal("bundle not in allowlist must fail")
	}
	web := baseView()
	if Match(&Targeting{AppBundlesAllow: []string{"com.example.game"}}, web) {
		t.Fatal("web request with app_bundles_allow must fail (no bundle)")
	}
	if !Match(&Targeting{AppBundlesBlock: []string{"com.example.game"}}, web) {
		t.Fatal("web request with only app_bundles_block must pass")
	}
}

func TestMatchPublisher(t *testing.T) {
	v := baseView()
	v.PubID = "pub-42"
	if Match(&Targeting{PublisherBlock: []string{"pub-42"}}, v) {
		t.Fatal("blocked publisher must fail")
	}
	if !Match(&Targeting{PublisherAllow: []string{"pub-42", "pub-7"}}, v) {
		t.Fatal("allowed publisher must pass")
	}
	if Match(&Targeting{PublisherAllow: []string{"pub-7"}}, v) {
		t.Fatal("publisher not in allowlist must fail")
	}
	noPub := baseView()
	if Match(&Targeting{PublisherAllow: []string{"pub-7"}}, noPub) {
		t.Fatal("missing publisher.id with allowlist must fail")
	}
	if !Match(&Targeting{PublisherBlock: []string{"pub-7"}}, noPub) {
		t.Fatal("missing publisher.id with blocklist only must pass")
	}
}

func TestMatchKeywords(t *testing.T) {
	v := baseView()
	if !Match(&Targeting{KeywordsAllow: []string{"Ипотека"}}, v) {
		t.Fatal("keywords_allow case-insensitive substring must pass")
	}
	if !Match(&Targeting{KeywordsAllow: []string{"москве"}}, v) {
		t.Fatal("content.title must be part of the haystack")
	}
	if !Match(&Targeting{KeywordsAllow: []string{"realty"}}, v) {
		t.Fatal("site.page URL must be part of the haystack")
	}
	if Match(&Targeting{KeywordsAllow: []string{"авто", "кредит"}}, v) {
		t.Fatal("no allow keyword present must fail")
	}
	if Match(&Targeting{KeywordsBlock: []string{"квартир"}}, v) {
		t.Fatal("keywords_block substring must fail")
	}
	if Match(&Targeting{KeywordsAllow: []string{"ипотека"}, KeywordsBlock: []string{"новостройки"}}, v) {
		t.Fatal("stop-word must win over allow keyword")
	}
	empty := baseView()
	empty.Keywords = ""
	if Match(&Targeting{KeywordsAllow: []string{"ипотека"}}, empty) {
		t.Fatal("request without keywords must fail keywords_allow")
	}
	if !Match(&Targeting{KeywordsBlock: []string{"ипотека"}}, empty) {
		t.Fatal("request without keywords must pass keywords_block")
	}
	if !Match(&Targeting{KeywordsAllow: []string{"", "  "}}, v) == false {
		// пустые строки в списке не должны матчить всё подряд
		t.Fatal("blank allow keywords must not match")
	}
}

func TestMatchIPRanges(t *testing.T) {
	v := baseView()
	v.IP = "192.168.10.5"
	if !Match(&Targeting{IPRanges: []string{"10.", "192.168."}}, v) {
		t.Fatal("prefix match expected")
	}
	if Match(&Targeting{IPRanges: []string{"10."}}, v) {
		t.Fatal("non-matching prefix must fail")
	}
	v.IP = "2a00:1450::1"
	if !Match(&Targeting{IPRanges: []string{"2a00:"}}, v) {
		t.Fatal("ipv6 prefix match expected")
	}
	v.IP = ""
	if Match(&Targeting{IPRanges: []string{"10."}}, v) {
		t.Fatal("missing ip with ip_ranges must fail")
	}
}

func TestMatchRegulatory(t *testing.T) {
	v := baseView()
	v.COPPA, v.GDPR, v.DNT = true, true, true
	if Match(&Targeting{BlockCOPPA: true}, v) {
		t.Fatal("block_coppa must reject coppa traffic")
	}
	if Match(&Targeting{BlockGDPR: true}, v) {
		t.Fatal("block_gdpr must reject gdpr traffic")
	}
	if Match(&Targeting{SkipDNT: true}, v) {
		t.Fatal("skip_dnt must reject dnt/lmt traffic")
	}
	if !Match(&Targeting{}, v) {
		t.Fatal("flags off: regulated traffic still allowed")
	}
	clean := baseView()
	if !Match(&Targeting{BlockCOPPA: true, BlockGDPR: true, SkipDNT: true}, clean) {
		t.Fatal("non-regulated traffic must pass all three flags")
	}
}

func TestMatchLanguageConnGenderAge(t *testing.T) {
	v := baseView()
	v.Language, v.ConnType, v.Gender, v.YOB = "ru", 2, "F", time.Now().UTC().Year()-30
	if !Match(&Targeting{Languages: []string{"RU"}, ConnectionTypes: []string{"wifi"}, Genders: []string{"f"}, AgeMin: 25, AgeMax: 34}, v) {
		t.Fatal("language/conn/gender/age must all match")
	}
	if Match(&Targeting{ConnectionTypes: []string{"cellular"}}, v) {
		t.Fatal("wifi must not match cellular")
	}
	if Match(&Targeting{AgeMax: 29}, v) {
		t.Fatal("age 30 > age_max 29 must fail")
	}
	v.YOB = 0
	if !Match(&Targeting{AgeMin: 25, AgeMax: 34}, v) {
		t.Fatal("unknown yob must pass age limits (Python: `and yob`)")
	}
}

func intp(n int) *int { return &n }

func TestMatchImp(t *testing.T) {
	if !MatchImp(nil, &ImpView{}) {
		t.Fatal("nil imp targeting must pass")
	}
	// banner_positions
	if MatchImp(&ImpTargeting{BannerPositions: []int{1}}, &ImpView{}) {
		t.Fatal("pos absent with banner_positions must fail")
	}
	if !MatchImp(&ImpTargeting{BannerPositions: []int{1, 3}}, &ImpView{Pos: intp(3)}) {
		t.Fatal("pos in list must pass")
	}
	if MatchImp(&ImpTargeting{BannerPositions: []int{1}}, &ImpView{Pos: intp(0)}) {
		t.Fatal("pos=0 (unknown) not in list must fail")
	}
	// interstitial
	if MatchImp(&ImpTargeting{InterstitialOnly: true}, &ImpView{}) {
		t.Fatal("interstitial_only requires instl=1")
	}
	if MatchImp(&ImpTargeting{ExcludeInterstitial: true}, &ImpView{Interstitial: true}) {
		t.Fatal("exclude_interstitial rejects instl=1")
	}
	// rewarded / secure
	if MatchImp(&ImpTargeting{RewardedOnly: true}, &ImpView{}) || !MatchImp(&ImpTargeting{RewardedOnly: true}, &ImpView{Rewarded: true}) {
		t.Fatal("rewarded_only semantics")
	}
	if MatchImp(&ImpTargeting{SecureOnly: true}, &ImpView{}) || !MatchImp(&ImpTargeting{SecureOnly: true}, &ImpView{Secure: true}) {
		t.Fatal("secure_only semantics")
	}
	// video
	vid := &ImpView{HasVideo: true, VideoMaxDuration: 15}
	if MatchImp(&ImpTargeting{VideoMinDuration: 30}, vid) {
		t.Fatal("maxduration 15 < video_min_duration 30 must fail")
	}
	if !MatchImp(&ImpTargeting{VideoMinDuration: 30}, &ImpView{HasVideo: true}) {
		t.Fatal("maxduration absent must pass (Python: `and video.get('maxduration')`)")
	}
	if !MatchImp(&ImpTargeting{VideoMinDuration: 30}, &ImpView{}) {
		t.Fatal("video constraints ignored for non-video imp")
	}
	if MatchImp(&ImpTargeting{VideoSkippableOnly: true}, vid) || !MatchImp(&ImpTargeting{VideoSkippableOnly: true}, &ImpView{HasVideo: true, VideoSkippable: true}) {
		t.Fatal("video_skippable_only semantics")
	}
	// deals
	if MatchImp(&ImpTargeting{DealIDs: []string{"d1"}}, &ImpView{}) {
		t.Fatal("deal_ids set, no pmp deals -> fail")
	}
	if !MatchImp(&ImpTargeting{DealIDs: []string{"d1", "d2"}}, &ImpView{DealIDs: []string{"x", "d2"}}) {
		t.Fatal("matching deal must pass")
	}
	if got := MatchedDeal([]string{"d1", "d2"}, []string{"x", "d2", "d1"}); got != "d2" {
		t.Fatalf("MatchedDeal: first present deal in imp order expected d2, got %q", got)
	}
	if got := MatchedDeal(nil, []string{"d1"}); got != "" {
		t.Fatalf("MatchedDeal with no wanted deals must be empty, got %q", got)
	}
}

func TestCompareVersion(t *testing.T) {
	cases := []struct {
		a, b string
		want int
	}{
		{"17.2", "16.4", 1}, {"16.4", "16.4", 0}, {"16.3", "16.4", -1},
		{"16", "16.4", -1}, {"16.4", "16", 1}, {"17", "16.4", 1}, {"16.4.9", "16.4.1", 0},
	}
	for _, c := range cases {
		if got := compareVersion(c.a, c.b); got != c.want {
			t.Errorf("compareVersion(%q,%q)=%d want %d", c.a, c.b, got, c.want)
		}
	}
}
