package rtb

// Пользовательский VAST (creatives.vast_xml) — audit R-05. Раньше он уходил
// в SSP как есть: ни нашего Impression, ни ClickTracking, ни квартилей — показы
// и клики по таким роликам не считались, CPC не списывался, erid терялся.
// Здесь VAST разбирается encoding/xml (строгая well-formedness), и наши
// трекеры вставляются по смещениям токенов — исходная разметка (CDATA,
// namespaces, сторонние трекеры) не переписывается. Зеркало —
// backend/rtb_compat.py::inject_vast_trackers; тесты фиксируют паритет.

import (
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
)

// vastTrackers — что вставляем в каждый <InLine>/<Wrapper>.
type vastTrackers struct {
	Impressions   []string // наш /api/rtb/imp + сторонний imp_tracker_url
	VeventBase    string   // …/api/rtb/vevent?bid=TOKEN — квартили строятся как &e=<event>
	ClickTracking []string // наш /api/rtb/click?…&beacon=1 + сторонний click_tracker_url
	ExtensionsXML string   // готовый <Extensions>…</Extensions> (erid/AdvertiserInfo) или ""
}

// vastEvents — VAST 3 §2.3.1.4 события, которые понимает backend /rtb/vevent.
var vastEvents = []string{"start", "firstQuartile", "midpoint", "thirdQuartile", "complete", "skip"}

var errVastNoAd = errors.New("vast: no <InLine> or <Wrapper>")

// vastInsert — точка вставки: байтовое смещение и текст.
type vastInsert struct {
	at   int
	text string
}

// adNode — один <InLine> или <Wrapper> с найденными потомками.
type adNode struct {
	name        string // InLine | Wrapper
	openEnd     int    // смещение сразу после '>' открывающего тега
	closeStart  int    // смещение '<' закрывающего тега
	lastImpEnd  int    // смещение после последнего </Impression> прямого потомка, 0 если нет
	hasErid     bool
	linears     []linearNode
	hasCreative bool // есть <Creatives> (Wrapper без него получит наш блок целиком)
}

type linearNode struct {
	openEnd         int
	trackingOpenEnd int // после '>' у <TrackingEvents>, 0 если нет
	clicksOpenEnd   int // после '>' у <VideoClicks>, 0 если нет
}

// injectVastTrackers вставляет трекеры во все InLine/Wrapper документа.
// Ошибка — XML не well-formed или нет ни одного Ad-контейнера: такой
// креатив не торгуется (adm_vast_malformed), а не уходит в SSP слепым.
func injectVastTrackers(vast string, tr vastTrackers) (string, error) {
	ads, err := scanVast(vast)
	if err != nil {
		return "", err
	}
	if len(ads) == 0 {
		return "", errVastNoAd
	}
	var inserts []vastInsert
	trackingXML := vastTrackingNodes(tr.VeventBase)
	clickXML := vastClickNodes(tr.ClickTracking)
	for _, ad := range ads {
		// Extensions — сразу после открывающего тега (как и раньше, R-04),
		// если ролик ещё не промаркирован.
		if tr.ExtensionsXML != "" && !ad.hasErid {
			inserts = append(inserts, vastInsert{ad.openEnd, tr.ExtensionsXML})
		}
		var imps strings.Builder
		for _, u := range tr.Impressions {
			if u != "" {
				fmt.Fprintf(&imps, "<Impression><![CDATA[%s]]></Impression>", u)
			}
		}
		if imps.Len() > 0 {
			at := ad.openEnd
			if ad.lastImpEnd > 0 {
				at = ad.lastImpEnd
			}
			inserts = append(inserts, vastInsert{at, imps.String()})
		}
		if len(ad.linears) == 0 {
			// Wrapper без Creatives: VAST 3 §2.4.1 разрешает Creatives/Linear с
			// TrackingEvents и ClickTracking в обёртке — добавляем свой блок.
			// InLine без Linear (companion/nonlinear) — только Impression.
			if ad.name == "Wrapper" && !ad.hasCreative && (trackingXML != "" || clickXML != "") {
				block := "<Creatives><Creative><Linear>"
				if trackingXML != "" {
					block += "<TrackingEvents>" + trackingXML + "</TrackingEvents>"
				}
				if clickXML != "" {
					block += "<VideoClicks>" + clickXML + "</VideoClicks>"
				}
				block += "</Linear></Creative></Creatives>"
				inserts = append(inserts, vastInsert{ad.closeStart, block})
			}
			continue
		}
		for _, ln := range ad.linears {
			if trackingXML != "" {
				if ln.trackingOpenEnd > 0 {
					inserts = append(inserts, vastInsert{ln.trackingOpenEnd, trackingXML})
				} else {
					inserts = append(inserts, vastInsert{ln.openEnd, "<TrackingEvents>" + trackingXML + "</TrackingEvents>"})
				}
			}
			if clickXML != "" {
				if ln.clicksOpenEnd > 0 {
					inserts = append(inserts, vastInsert{ln.clicksOpenEnd, clickXML})
				} else {
					inserts = append(inserts, vastInsert{ln.openEnd, "<VideoClicks>" + clickXML + "</VideoClicks>"})
				}
			}
		}
	}
	return applyInserts(vast, inserts), nil
}

func vastTrackingNodes(vevent string) string {
	if vevent == "" {
		return ""
	}
	var b strings.Builder
	for _, e := range vastEvents {
		fmt.Fprintf(&b, `<Tracking event="%s"><![CDATA[%s&e=%s]]></Tracking>`, e, vevent, e)
	}
	return b.String()
}

func vastClickNodes(urls []string) string {
	var b strings.Builder
	for _, u := range urls {
		if u != "" {
			fmt.Fprintf(&b, "<ClickTracking><![CDATA[%s]]></ClickTracking>", u)
		}
	}
	return b.String()
}

// applyInserts собирает строку с вставками; стабильная сортировка по
// возрастанию сохраняет порядок нескольких вставок в одной точке.
func applyInserts(s string, ins []vastInsert) string {
	sort.SliceStable(ins, func(i, j int) bool { return ins[i].at < ins[j].at })
	var b strings.Builder
	b.Grow(len(s) + 1024)
	prev := 0
	for _, in := range ins {
		b.WriteString(s[prev:in.at])
		b.WriteString(in.text)
		prev = in.at
	}
	b.WriteString(s[prev:])
	return b.String()
}

// scanVast — один проход xml.Decoder: well-formedness + смещения нужных
// элементов. Strict, но с HTML-сущностями (&nbsp; в AdTitle встречается).
func scanVast(vast string) ([]adNode, error) {
	d := xml.NewDecoder(strings.NewReader(vast))
	d.Strict = true
	d.Entity = xml.HTMLEntity
	var (
		ads      []adNode
		stack    []string
		selfCl   []bool // параллельно stack: тег был <X/>
		sawRoot  bool
		cur      *adNode // текущий InLine/Wrapper
		curLin   *linearNode
		adDepth  int // глубина стека, на которой открыт cur
		linDepth int
	)
	for {
		tok, err := d.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("vast: %w", err)
		}
		switch t := tok.(type) {
		case xml.StartElement:
			name := t.Name.Local
			if !sawRoot {
				if name != "VAST" {
					return nil, errors.New("vast: root element is not <VAST>")
				}
				sawRoot = true
			}
			stack = append(stack, name)
			end := int(d.InputOffset())
			selfCl = append(selfCl, strings.HasSuffix(strings.TrimSpace(vast[:end]), "/>"))
			switch {
			case (name == "InLine" || name == "Wrapper") && cur == nil:
				ads = append(ads, adNode{name: name, openEnd: end})
				cur = &ads[len(ads)-1]
				adDepth = len(stack)
			case cur != nil && name == "Creatives" && len(stack) == adDepth+1:
				cur.hasCreative = true
			case cur != nil && name == "Linear":
				cur.linears = append(cur.linears, linearNode{openEnd: end})
				curLin = &cur.linears[len(cur.linears)-1]
				linDepth = len(stack)
			case curLin != nil && name == "TrackingEvents" && len(stack) == linDepth+1:
				curLin.trackingOpenEnd = end
			case curLin != nil && name == "VideoClicks" && len(stack) == linDepth+1:
				curLin.clicksOpenEnd = end
			case cur != nil && name == "Erid":
				cur.hasErid = true
			}
		case xml.EndElement:
			if len(stack) == 0 {
				return nil, errors.New("vast: unbalanced end tag")
			}
			name := t.Name.Local
			depth := len(stack)
			stack = stack[:depth-1]
			closed := selfCl[depth-1]
			selfCl = selfCl[:depth-1]
			end := int(d.InputOffset())
			if cur != nil && depth == adDepth+1 && name == "Impression" {
				cur.lastImpEnd = end
			}
			if curLin != nil && depth == linDepth {
				curLin = nil
			}
			if cur != nil && depth == adDepth {
				if closed {
					// <InLine/> — пустой контейнер, вставлять некуда; не считаем его Ad.
					ads = ads[:len(ads)-1]
				} else {
					// Закрывающий тег начинается на последнем '<' перед end
					// (устойчиво к префиксам </vast:InLine> и пробелам).
					cur.closeStart = strings.LastIndex(vast[:end], "<")
				}
				cur = nil
			}
		}
	}
	if !sawRoot {
		return nil, errors.New("vast: empty document")
	}
	return ads, nil
}
