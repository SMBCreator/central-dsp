package rtb

// 38-ФЗ (маркировка рекламы): в каждом формате, уходящем в РФ-трафик, adm
// должен нести erid, пометку «Реклама» и сведения о рекламодателе. Плюс
// экранирование — данные рекламодателя приходят из БД и попадают в HTML/XML.

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

func markedCandidate(typ string) *feed.Candidate {
	w, h := 300, 250
	return &feed.Candidate{
		CreativeID: "11111111-2222-3333-4444-555555555555",
		Type:       typ,
		Width:      &w,
		Height:     &h,
		AssetURL:   "https://cdn.example/banner.png",
		ClickURL:   "https://adv.example/landing",
		VideoURL:   "https://cdn.example/v.mp4",
		Name:       "Осенняя распродажа",
		Erid:       "2VtzqxABCDE",
		AdvName:    `ООО "Ромашка" <и партнёры>`,
		AdvINN:     "7700000001",
		AdvOGRN:    "1027700000001",
		AdvAddress: "Москва, ул. Пушкина, д. 1",
	}
}

const testBase = "https://dsp.example"

func TestBuildAdm_BannerCarriesEridAndLabel(t *testing.T) {
	adm := BuildAdm(markedCandidate("banner"), testBase, "tok")
	for _, want := range []string{
		"erid: 2VtzqxABCDE",
		"Реклама",
		"ИНН: 7700000001",
		"ОГРН: 1027700000001",
		"Москва, ул. Пушкина",
		testBase + "/api/rtb/imp?bid=tok",
		testBase + "/api/rtb/click?bid=tok",
	} {
		if !strings.Contains(adm, want) {
			t.Errorf("banner adm lacks %q\n%s", want, adm)
		}
	}
	// Данные рекламодателя экранированы — иначе кавычки/угловые скобки ломают HTML.
	if strings.Contains(adm, `ООО "Ромашка" <и партнёры>`) {
		t.Error("advertiser name must be HTML-escaped")
	}
	if !strings.Contains(adm, "ООО &quot;Ромашка&quot; &lt;и партнёры&gt;") {
		t.Errorf("escaped advertiser name missing\n%s", adm)
	}
}

func TestBuildAdm_BannerWithHTMLSnippetStillMarked(t *testing.T) {
	c := markedCandidate("banner")
	c.HTMLSnippet = `<div id="ad">custom</div>`
	adm := BuildAdm(c, testBase, "tok")
	// Сниппет — как есть, внутри обёртки для viewability (паритет с rtb.py).
	if !strings.Contains(adm, `<div id="cdsp_tok_c">`+c.HTMLSnippet+`</div>`) {
		t.Fatalf("snippet must be preserved as-is inside the wrapper\n%s", adm)
	}
	if !strings.Contains(adm, "erid: 2VtzqxABCDE") || !strings.Contains(adm, "Реклама") {
		t.Errorf("custom HTML banner must still carry the ad-info badge\n%s", adm)
	}
	if !strings.Contains(adm, "/api/rtb/imp?bid=tok") {
		t.Error("impression pixel missing for HTML snippet banner")
	}
}

func TestBuildAdm_NativeExtCarriesEridAndAdvertiser(t *testing.T) {
	for _, typ := range []string{"native", "push"} {
		adm := BuildAdm(markedCandidate(typ), testBase, "tok")
		var doc struct {
			Native struct {
				Ext struct {
					Erid     string            `json:"erid"`
					Adv      map[string]string `json:"advertiser_info"`
					AdFormat string            `json:"ad_format"`
				} `json:"ext"`
				Imptrackers []string `json:"imptrackers"`
			} `json:"native"`
		}
		if err := json.Unmarshal([]byte(adm), &doc); err != nil {
			t.Fatalf("%s adm is not valid JSON: %v\n%s", typ, err, adm)
		}
		if doc.Native.Ext.Erid != "2VtzqxABCDE" {
			t.Errorf("%s: ext.erid = %q", typ, doc.Native.Ext.Erid)
		}
		adv := doc.Native.Ext.Adv
		if adv["name"] != `ООО "Ромашка" <и партнёры>` || adv["inn"] != "7700000001" || adv["ogrn"] != "1027700000001" {
			t.Errorf("%s: advertiser_info incomplete: %v", typ, adv)
		}
		if len(doc.Native.Imptrackers) == 0 || !strings.Contains(doc.Native.Imptrackers[0], "/api/rtb/imp?bid=tok") {
			t.Errorf("%s: imptrackers missing our pixel: %v", typ, doc.Native.Imptrackers)
		}
		if typ == "push" && doc.Native.Ext.AdFormat != "push" {
			t.Error("push must set ext.ad_format=push")
		}
	}
}

func TestBuildAdm_VideoVASTCarriesEridExtension(t *testing.T) {
	adm := BuildAdm(markedCandidate("video"), testBase, "tok")
	for _, want := range []string{
		`<Extension type="erid"><Erid>2VtzqxABCDE</Erid></Extension>`,
		`<Extension type="AdvertiserInfo">`,
		`<INN>7700000001</INN>`,
		"ООО &quot;Ромашка&quot; &lt;и партнёры&gt;",
		"/api/rtb/imp?bid=tok",
		"/api/rtb/vevent?bid=tok&e=complete",
	} {
		if !strings.Contains(adm, want) {
			t.Errorf("VAST lacks %q\n%s", want, adm)
		}
	}
	if strings.Contains(adm, `<Name>ООО "Ромашка"`) {
		t.Error("advertiser name must be XML-escaped in VAST")
	}
}

// Пользовательский VAST (vast_xml из креатива) тоже должен быть промаркирован:
// erid выдан на это содержимое, и без Extension площадка покажет его как
// немаркированную рекламу.
func TestBuildAdm_CustomVASTGetsEridInjected(t *testing.T) {
	c := markedCandidate("video")
	c.VastXML = `<VAST version="3.0"><Ad id="x"><InLine><AdSystem>Ext</AdSystem><AdTitle>t</AdTitle><Impression><![CDATA[https://third/imp]]></Impression><Creatives></Creatives></InLine></Ad></VAST>`
	adm := BuildAdm(c, testBase, "tok")
	if !strings.Contains(adm, "<Erid>2VtzqxABCDE</Erid>") {
		t.Errorf("custom VAST must carry erid Extension\n%s", adm)
	}
	if strings.Count(adm, "<Extensions>") != 1 {
		t.Errorf("exactly one <Extensions> block expected\n%s", adm)
	}
	if !strings.Contains(adm, "<AdSystem>Ext</AdSystem>") || !strings.Contains(adm, "https://third/imp") {
		t.Error("original VAST content must be preserved")
	}
	// Уже промаркированный VAST не дублируем.
	c.VastXML = `<VAST version="3.0"><Ad id="x"><InLine><Extensions><Extension type="erid"><Erid>2VtzqxABCDE</Erid></Extension></Extensions><AdSystem>Ext</AdSystem></InLine></Ad></VAST>`
	adm = BuildAdm(c, testBase, "tok")
	if strings.Count(adm, "<Erid>") != 1 {
		t.Errorf("pre-marked VAST must not get a second erid\n%s", adm)
	}
}

// Без erid и без рекламодателя (не-РФ гео, фид пропустил): бейдж не рисуем,
// но пиксели и клик на месте. Это единственный легальный путь adm без erid —
// в РФ-фид такой креатив не попадает (backend ERID_FEED_GATE_SQL).
func TestBuildAdm_NoEridNoBadgeButTrackersIntact(t *testing.T) {
	c := markedCandidate("banner")
	c.Erid, c.AdvName, c.AdvINN, c.AdvOGRN, c.AdvAddress = "", "", "", "", ""
	adm := BuildAdm(c, testBase, "tok")
	if strings.Contains(adm, "Реклама") || strings.Contains(adm, "erid") {
		t.Errorf("unmarked candidate must not render an empty badge\n%s", adm)
	}
	if !strings.Contains(adm, "/api/rtb/imp?bid=tok") || !strings.Contains(adm, "/api/rtb/click?bid=tok") {
		t.Error("trackers must remain")
	}
}
