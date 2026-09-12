package rtb

// Audit R-05: трекеры во всех форматах, экранирование по контексту, отказ
// от небезопасных URL и невалидного VAST, клик без open redirect.

import (
	"encoding/json"
	"encoding/xml"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

const tok = "v1.9d3c2f1e-1111-2222-3333-444455556666.1800000000.abcdef0123456789"

func r05Candidate(typ string) *feed.Candidate {
	c := markedCandidate(typ)
	c.ImpTrackerURL = "https://audit.example/imp?a=1&b=2"
	c.ClickTrackerURL = "https://audit.example/click?x=1&y=${AUCTION_ID}"
	c.NativeIconURL = "https://cdn.example/icon.png"
	c.NativeTitle = "Заголовок"
	return c
}

// --- safeURL / admIncompat -------------------------------------------------

func TestSafeURL(t *testing.T) {
	ok := []string{
		"https://adv.example/l?utm=1&x=2",
		"http://a.b/c",
		"https://ssp.example/p?price=${AUCTION_PRICE}&id={ID}",
	}
	for _, u := range ok {
		if !safeURL(u) {
			t.Errorf("%q must be accepted", u)
		}
	}
	bad := []string{
		"", "javascript:alert(1)", "data:text/html,x", "//evil.example/x", "ftp://a/b",
		"https://a.b/c d", "https://a.b/c\"onload=alert(1)", "https://a.b/c'x", "https://a.b/<x>",
		"https://a.b/\\x", "https:///nohost", "https://a.b/\n", "https://a.b/" + strings.Repeat("x", 2048),
	}
	for _, u := range bad {
		if safeURL(u) {
			t.Errorf("%q must be rejected", u)
		}
	}
}

func TestAdmIncompat_Reasons(t *testing.T) {
	c := r05Candidate("banner")
	if r := admIncompat(c); r != "" {
		t.Fatalf("valid banner: %q", r)
	}
	c.ClickURL = "javascript:alert(1)"
	if r := admIncompat(c); r != IncompatAdmURL {
		t.Errorf("javascript click_url: %q", r)
	}
	c = r05Candidate("banner")
	c.AssetURL = `https://cdn.example/x.png" onerror="alert(1)`
	if r := admIncompat(c); r != IncompatAdmURL {
		t.Errorf("asset injection: %q", r)
	}
	// HTML5-сниппет без asset_url — валиден.
	c.AssetURL = ""
	c.HTMLSnippet = "<div>x</div>"
	if r := admIncompat(c); r != "" {
		t.Errorf("html5 without asset: %q", r)
	}
	c = r05Candidate("native")
	c.NativeIconURL = "data:image/png;base64,AAAA"
	if r := admIncompat(c); r != IncompatAdmURL {
		t.Errorf("native icon data: URL: %q", r)
	}
	c = r05Candidate("video")
	c.ImpTrackerURL = "https://x.y/p?a=1 b"
	if r := admIncompat(c); r != IncompatAdmURL {
		t.Errorf("tracker with space: %q", r)
	}
	c = r05Candidate("video")
	c.VastXML = "<VAST version=\"3.0\"><Ad><InLine><AdSystem>x</AdSystem></Ad></VAST>"
	if r := admIncompat(c); r != IncompatAdmVAST {
		t.Errorf("malformed VAST: %q", r)
	}
	c.VastXML = "<VAST version=\"3.0\"><Ad id=\"1\"></Ad></VAST>"
	if r := admIncompat(c); r != IncompatAdmVAST {
		t.Errorf("VAST without InLine/Wrapper: %q", r)
	}
	c.VastXML = "<html><body>not vast</body></html>"
	if r := admIncompat(c); r != IncompatAdmVAST {
		t.Errorf("non-VAST root: %q", r)
	}
}

// --- banner ------------------------------------------------------------------

func TestBanner_NoOpenRedirectParamAndEscapedThirdParty(t *testing.T) {
	c := r05Candidate("banner")
	c.AssetURL = "https://cdn.example/b.png?a=1&b=2"
	adm := BuildAdm(c, testBase, tok)
	if strings.Contains(adm, "&r=") || strings.Contains(adm, "r=https") {
		t.Errorf("click URL must not carry a redirect target\n%s", adm)
	}
	if !strings.Contains(adm, `href="`+testBase+`/api/rtb/click?bid=`+tok+`"`) {
		t.Errorf("click href missing\n%s", adm)
	}
	// asset_url — в атрибуте: & -> &amp;
	if !strings.Contains(adm, `src="https://cdn.example/b.png?a=1&amp;b=2"`) {
		t.Errorf("asset_url must be attribute-escaped\n%s", adm)
	}
	// сторонний imp-пиксель — в атрибуте, сторонний клик — в JS-строке (\x27 вместо кавычки)
	if !strings.Contains(adm, `src="https://audit.example/imp?a=1&amp;b=2"`) {
		t.Errorf("third-party imp pixel must be attribute-escaped\n%s", adm)
	}
	if !strings.Contains(adm, `onclick="(new Image()).src='https://audit.example/click?x=1\x26y=${AUCTION_ID}'"`) {
		t.Errorf("third-party click tracker must be JS-escaped\n%s", adm)
	}
	// Защитный слой: даже если кавычка проскочит валидатор, в JS-строку она
	// попадает как \x27, а не как разделитель.
	if got := jsStringEscape(`https://a/b?q='x'&z="y"<s>`); got != `https://a/b?q=\x27x\x27\x26z=\x22y\x22\x3cs\x3e` {
		t.Errorf("jsStringEscape = %q", got)
	}
	// viewability (паритет с rtb.py): обёртка + IntersectionObserver + /rtb/view
	for _, want := range []string{`<div id="cdsp_v1.9d3c2f1e-1111-2222-33"`, "IntersectionObserver", "intersectionRatio>=0.5", "},1000)", "/api/rtb/view?bid=" + tok} {
		if !strings.Contains(adm, want) {
			t.Errorf("banner lacks %q\n%s", want, adm)
		}
	}
}

func TestBannerHTML5_ClickMacroSubstituted(t *testing.T) {
	c := r05Candidate("banner")
	c.AssetURL = ""
	c.HTMLSnippet = `<a href="${CLICK_URL}"><img src="https://cdn.example/x.png"></a><script>var u="%%CLICK_URL_ESC%%";</script>`
	adm := BuildAdm(c, testBase, tok)
	click := testBase + "/api/rtb/click?bid=" + tok
	if !strings.Contains(adm, `href="`+click+`"`) {
		t.Errorf("${CLICK_URL} must be replaced\n%s", adm)
	}
	if !strings.Contains(adm, `var u="https%3A%2F%2Fdsp.example%2Fapi%2Frtb%2Fclick%3Fbid%3D`) {
		t.Errorf("%%%%CLICK_URL_ESC%%%% must be URL-encoded\n%s", adm)
	}
	if strings.Contains(adm, "CLICK_URL") {
		t.Error("macro left unreplaced")
	}
	// С макросом маячок клика на обёртке не нужен — клик идёт через /click.
	if strings.Contains(adm, "&beacon=1") {
		t.Error("beacon must not be added when the snippet uses the click macro")
	}
}

func TestBannerHTML5_NoMacroGetsClickBeacon(t *testing.T) {
	c := r05Candidate("banner")
	c.AssetURL = ""
	c.HTMLSnippet = `<a href="https://adv.example/direct"><img src="https://cdn.example/x.png"></a>`
	adm := BuildAdm(c, testBase, tok)
	if !strings.Contains(adm, `addEventListener("click"`) || !strings.Contains(adm, "/api/rtb/click?bid="+tok+"&beacon=1") {
		t.Errorf("snippet without macro must get a capture-phase click beacon\n%s", adm)
	}
	if !strings.Contains(adm, `getElementById("cdsp_v1.9d3c2f1e-1111-2222-33_c")`) {
		t.Errorf("beacon must listen on the content wrapper, not on the ad badge\n%s", adm)
	}
	if !strings.Contains(adm, c.HTMLSnippet) {
		t.Error("snippet must be preserved as-is")
	}
}

// --- native ---------------------------------------------------------------------

func TestNative_EventtrackersViewableAndNoRedirectParam(t *testing.T) {
	adm := BuildAdm(r05Candidate("native"), testBase, tok)
	var doc struct {
		Native struct {
			Link struct {
				URL           string   `json:"url"`
				Clicktrackers []string `json:"clicktrackers"`
			} `json:"link"`
			Eventtrackers []struct {
				Event   int    `json:"event"`
				Methods []int  `json:"methods"`
				URL     string `json:"url"`
			} `json:"eventtrackers"`
		} `json:"native"`
	}
	if err := json.Unmarshal([]byte(adm), &doc); err != nil {
		t.Fatalf("invalid JSON: %v\n%s", err, adm)
	}
	if doc.Native.Link.URL != testBase+"/api/rtb/click?bid="+tok {
		t.Errorf("link.url = %q", doc.Native.Link.URL)
	}
	if len(doc.Native.Eventtrackers) != 1 || doc.Native.Eventtrackers[0].Event != 2 ||
		doc.Native.Eventtrackers[0].Methods[0] != 1 || !strings.Contains(doc.Native.Eventtrackers[0].URL, "/api/rtb/view?bid="+tok) {
		t.Errorf("eventtrackers must carry MRC viewable (event=2, method=1): %+v", doc.Native.Eventtrackers)
	}
	if len(doc.Native.Link.Clicktrackers) != 1 || doc.Native.Link.Clicktrackers[0] != "https://audit.example/click?x=1&y=${AUCTION_ID}" {
		t.Errorf("third-party clicktracker must be passed verbatim in JSON: %v", doc.Native.Link.Clicktrackers)
	}
}

// --- video --------------------------------------------------------------------

func mustParseXML(t *testing.T, s string) {
	t.Helper()
	d := xml.NewDecoder(strings.NewReader(s))
	for {
		_, err := d.Token()
		if err != nil {
			if err.Error() == "EOF" {
				return
			}
			t.Fatalf("adm is not well-formed XML: %v\n%s", err, s)
		}
	}
}

func TestVideoGenerated_ClickThroughIsOurRedirectAndBeacon(t *testing.T) {
	adm := BuildAdm(r05Candidate("video"), testBase, tok)
	mustParseXML(t, adm)
	click := testBase + "/api/rtb/click?bid=" + tok
	for _, want := range []string{
		"<ClickThrough><![CDATA[" + click + "]]></ClickThrough>",
		"<ClickTracking><![CDATA[" + click + "&beacon=1]]></ClickTracking>",
		"<ClickTracking><![CDATA[https://audit.example/click?x=1&y=${AUCTION_ID}]]></ClickTracking>",
		"<Impression><![CDATA[https://audit.example/imp?a=1&b=2]]></Impression>",
		`<Tracking event="firstQuartile"><![CDATA[` + testBase + "/api/rtb/vevent?bid=" + tok + "&e=firstQuartile]]></Tracking>",
	} {
		if !strings.Contains(adm, want) {
			t.Errorf("VAST lacks %q\n%s", want, adm)
		}
	}
	if strings.Contains(adm, "&r=") {
		t.Error("VAST click must not carry redirect target")
	}
}

const customInline = `<?xml version="1.0" encoding="UTF-8"?>
<VAST version="3.0"><Ad id="ext-1"><InLine>
<AdSystem>Ext</AdSystem><AdTitle>t &amp; q</AdTitle>
<Impression id="third"><![CDATA[https://third/imp?a=1&b=2]]></Impression>
<Creatives><Creative><Linear skipoffset="00:00:05"><Duration>00:00:30</Duration>
<TrackingEvents><Tracking event="start"><![CDATA[https://third/start]]></Tracking></TrackingEvents>
<VideoClicks><ClickThrough><![CDATA[https://adv/landing]]></ClickThrough></VideoClicks>
<MediaFiles><MediaFile type="video/mp4" delivery="progressive" width="640" height="360"><![CDATA[https://cdn/v.mp4]]></MediaFile></MediaFiles>
</Linear></Creative></Creatives></InLine></Ad></VAST>`

func TestCustomVAST_InLine_GetsAllTrackers(t *testing.T) {
	c := r05Candidate("video")
	c.VastXML = customInline
	adm := BuildAdm(c, testBase, tok)
	if adm == "" {
		t.Fatal("adm must be built")
	}
	mustParseXML(t, adm)
	base := testBase + "/api/rtb/"
	for _, want := range []string{
		"<Impression><![CDATA[" + base + "imp?bid=" + tok + "]]></Impression>",
		"<Impression><![CDATA[https://audit.example/imp?a=1&b=2]]></Impression>",
		`<Tracking event="start"><![CDATA[` + base + "vevent?bid=" + tok + "&e=start]]></Tracking>",
		`<Tracking event="complete"><![CDATA[` + base + "vevent?bid=" + tok + "&e=complete]]></Tracking>",
		`<Tracking event="skip">`,
		"<ClickTracking><![CDATA[" + base + "click?bid=" + tok + "&beacon=1]]></ClickTracking>",
		"<ClickTracking><![CDATA[https://audit.example/click?x=1&y=${AUCTION_ID}]]></ClickTracking>",
		"<Erid>2VtzqxABCDE</Erid>",
		// исходное содержимое не тронуто
		"<![CDATA[https://third/imp?a=1&b=2]]>", "<![CDATA[https://third/start]]>", "<![CDATA[https://adv/landing]]>",
		`<Linear skipoffset="00:00:05">`, "<AdTitle>t &amp; q</AdTitle>",
	} {
		if !strings.Contains(adm, want) {
			t.Errorf("custom VAST lacks %q\n%s", want, adm)
		}
	}
	if strings.Count(adm, "<TrackingEvents>") != 1 || strings.Count(adm, "<VideoClicks>") != 1 {
		t.Errorf("must reuse existing TrackingEvents/VideoClicks containers\n%s", adm)
	}
	// Наш Impression — после стороннего (порядок в схеме сохраняем), ClickThrough не заменён.
	if strings.Index(adm, "third/imp") > strings.Index(adm, "imp?bid="+tok) {
		t.Error("our Impression must follow the existing ones")
	}
	if strings.Count(adm, "<ClickThrough>") != 1 {
		t.Error("existing ClickThrough must be kept exactly once")
	}
}

func TestCustomVAST_Wrapper_WithoutCreativesGetsLinearBlock(t *testing.T) {
	c := r05Candidate("video")
	c.ImpTrackerURL, c.ClickTrackerURL = "", ""
	c.VastXML = `<VAST version="3.0"><Ad id="w"><Wrapper><AdSystem>W</AdSystem><VASTAdTagURI><![CDATA[https://third/tag.xml]]></VASTAdTagURI><Impression><![CDATA[https://third/imp]]></Impression></Wrapper></Ad></VAST>`
	adm := BuildAdm(c, testBase, tok)
	mustParseXML(t, adm)
	for _, want := range []string{
		"<Impression><![CDATA[" + testBase + "/api/rtb/imp?bid=" + tok + "]]></Impression>",
		"<Creatives><Creative><Linear><TrackingEvents>",
		`<Tracking event="midpoint">`,
		"<VideoClicks><ClickTracking><![CDATA[" + testBase + "/api/rtb/click?bid=" + tok + "&beacon=1]]></ClickTracking></VideoClicks>",
		"</Linear></Creative></Creatives></Wrapper>",
		"<Erid>2VtzqxABCDE</Erid>",
	} {
		if !strings.Contains(adm, want) {
			t.Errorf("wrapper VAST lacks %q\n%s", want, adm)
		}
	}
	if strings.Count(adm, "<ClickThrough>") != 0 {
		t.Error("wrapper must not get a ClickThrough (VAST 3 §2.4.1.6)")
	}
}

func TestCustomVAST_LinearWithoutContainersGetsThem(t *testing.T) {
	c := r05Candidate("video")
	c.VastXML = `<VAST version="2.0"><Ad id="a"><InLine><AdSystem>x</AdSystem><AdTitle>y</AdTitle><Creatives><Creative><Linear><Duration>00:00:10</Duration><MediaFiles><MediaFile type="video/mp4"><![CDATA[https://cdn/v.mp4]]></MediaFile></MediaFiles></Linear></Creative></Creatives></InLine></Ad></VAST>`
	adm := BuildAdm(c, testBase, tok)
	mustParseXML(t, adm)
	if !strings.Contains(adm, "<Linear><TrackingEvents>") && !strings.Contains(adm, "<Linear><VideoClicks>") {
		t.Errorf("containers must be created inside Linear\n%s", adm)
	}
	if strings.Count(adm, "<TrackingEvents>") != 1 || strings.Count(adm, "<VideoClicks>") != 1 {
		t.Errorf("exactly one container of each kind\n%s", adm)
	}
	// Impression без существующих — сразу после <InLine>
	if !strings.Contains(adm, "<InLine>") {
		t.Fatal("InLine lost")
	}
}

func TestCustomVAST_MultipleAdsPod(t *testing.T) {
	c := r05Candidate("video")
	c.ImpTrackerURL, c.ClickTrackerURL = "", ""
	c.VastXML = `<VAST version="3.0"><Ad id="1" sequence="1"><InLine><AdSystem>x</AdSystem><Creatives><Creative><Linear><Duration>00:00:10</Duration></Linear></Creative></Creatives></InLine></Ad><Ad id="2" sequence="2"><InLine><AdSystem>x</AdSystem><Creatives><Creative><Linear><Duration>00:00:10</Duration></Linear></Creative></Creatives></InLine></Ad></VAST>`
	adm := BuildAdm(c, testBase, tok)
	mustParseXML(t, adm)
	if n := strings.Count(adm, "imp?bid="+tok); n != 2 {
		t.Errorf("each Ad in a pod must get our Impression, got %d\n%s", n, adm)
	}
	if n := strings.Count(adm, "&e=complete"); n != 2 {
		t.Errorf("each Linear must get TrackingEvents, got %d", n)
	}
}

func TestCustomVAST_MalformedYieldsEmptyAdm(t *testing.T) {
	c := r05Candidate("video")
	c.VastXML = `<VAST version="3.0"><Ad><InLine><AdSystem>x</Ad></VAST>`
	if adm := BuildAdm(c, testBase, tok); adm != "" {
		t.Errorf("malformed VAST must yield empty adm (no-bid), got\n%s", adm)
	}
}

func TestCustomVAST_PrefixedAndHTMLEntitiesTolerated(t *testing.T) {
	c := r05Candidate("video")
	c.ImpTrackerURL, c.ClickTrackerURL = "", ""
	c.VastXML = `<VAST version="3.0"><Ad id="a"><Wrapper ><AdSystem>x&nbsp;y</AdSystem><VASTAdTagURI>https://t/x</VASTAdTagURI></Wrapper ></Ad></VAST>`
	adm := BuildAdm(c, testBase, tok)
	if adm == "" {
		t.Fatal("HTML entity / spaced close tag must be tolerated")
	}
	if !strings.Contains(adm, "</Creatives></Wrapper >") {
		t.Errorf("block must be inserted right before the Wrapper close tag\n%s", adm)
	}
}

func TestCustomVAST_SelfClosingContainerIsNotAnAd(t *testing.T) {
	c := r05Candidate("video")
	c.VastXML = `<VAST version="3.0"><Ad id="a"><InLine/></Ad></VAST>`
	if r := admIncompat(c); r != IncompatAdmVAST {
		t.Errorf("empty <InLine/> must be treated as no Ad, got %q", r)
	}
	c.ImpTrackerURL, c.ClickTrackerURL = "", ""
	c.VastXML = `<VAST version="3.0"><Ad id="w"><Wrapper><AdSystem>x</AdSystem><VASTAdTagURI>https://t/x</VASTAdTagURI><Impression/></Wrapper></Ad></VAST>`
	adm := BuildAdm(c, testBase, tok)
	mustParseXML(t, adm)
	if !strings.Contains(adm, "<Impression/><Impression><![CDATA["+testBase+"/api/rtb/imp?bid="+tok+"]]></Impression>") {
		t.Errorf("our Impression must follow the self-closing one\n%s", adm)
	}
}
