// Package rtb: OpenRTB 2.5 модели (подмножество, используемое Central DSP).
// Паритет полей — с backend/routers/rtb.py (Python reference).
package rtb

import "encoding/json"

// BidRequest — OpenRTB 2.5 bid request.
type BidRequest struct {
	ID     string          `json:"id"`
	Imp    []Imp           `json:"imp"`
	Site   *Site           `json:"site,omitempty"`
	App    *App            `json:"app,omitempty"`
	Device *Device         `json:"device,omitempty"`
	User   *User           `json:"user,omitempty"`
	At     int             `json:"at,omitempty"` // 1=first price, 2=second price (default). Shading только при at=1.
	TMax   int             `json:"tmax,omitempty"`
	Cur    []string        `json:"cur,omitempty"` // Central DSP биддит только в RUB
	BAdv   []string        `json:"badv,omitempty"`
	BCat   []string        `json:"bcat,omitempty"` // IAB-категории, запрещённые издателем
	Test   int             `json:"test,omitempty"` // 1 = тестовый запрос: бид считаем, деньги не списываем
	Regs   *Regs           `json:"regs,omitempty"`
	Source *Source         `json:"source,omitempty"`
	Ext    json.RawMessage `json:"ext,omitempty"`
}

// Source — источник запроса; schain нужен для «только прямые продавцы».
type Source struct {
	SChain *SupplyChain    `json:"schain,omitempty"`
	Ext    json.RawMessage `json:"ext,omitempty"` // OpenRTB 2.5: source.ext.schain
}

// SupplyChain — IAB Supply Chain Object (sellers.json / ads.txt).
type SupplyChain struct {
	Complete int               `json:"complete,omitempty"`
	Nodes    []SupplyChainNode `json:"nodes,omitempty"`
}

type SupplyChainNode struct {
	ASI string `json:"asi,omitempty"` // домен продавца
	SID string `json:"sid,omitempty"` // seller id
	HP  int    `json:"hp,omitempty"`  // 1 = участвует в платеже
}

// Regs — регуляторные флаги (block_coppa / block_gdpr в таргетинге).
type Regs struct {
	COPPA int             `json:"coppa,omitempty"`
	GDPR  int             `json:"gdpr,omitempty"` // OpenRTB 2.6; в 2.5 — regs.ext.gdpr
	Ext   json.RawMessage `json:"ext,omitempty"`
}

type Imp struct {
	ID          string          `json:"id"`
	TagID       string          `json:"tagid,omitempty"` // id рекламного места у SSP — разрез отчёта «по площадкам»
	Banner      *Banner         `json:"banner,omitempty"`
	Video       *Video          `json:"video,omitempty"`
	Native      *Native         `json:"native,omitempty"`
	Instl       int             `json:"instl,omitempty"`  // 1 = interstitial
	Secure      int             `json:"secure,omitempty"` // 1 = HTTPS-only
	Rwdd        int             `json:"rwdd,omitempty"`   // OpenRTB 2.6 rewarded; fallback ext.rewarded
	PMP         *PMP            `json:"pmp,omitempty"`
	BidFloor    float64         `json:"bidfloor,omitempty"`    // floor сравнивается в Decimal-семантике: см. money notes в спеке
	BidFloorCur string          `json:"bidfloorcur,omitempty"` // ожидаем RUB
	Ext         json.RawMessage `json:"ext,omitempty"`
}

// PMP — private marketplace (deal_ids в таргетинге).
type PMP struct {
	PrivateAuction int    `json:"private_auction,omitempty"`
	Deals          []Deal `json:"deals,omitempty"`
}

type Deal struct {
	ID          string  `json:"id"`
	BidFloor    float64 `json:"bidfloor,omitempty"`
	BidFloorCur string  `json:"bidfloorcur,omitempty"`
	At          int     `json:"at,omitempty"`
}

type Banner struct {
	W      int      `json:"w,omitempty"`
	H      int      `json:"h,omitempty"`
	Format []Format `json:"format,omitempty"`
	Pos    *int     `json:"pos,omitempty"` // OpenRTB AdPosition; nil = не передан
}

type Format struct {
	W int `json:"w"`
	H int `json:"h"`
}

type Video struct {
	Mimes       []string `json:"mimes,omitempty"`
	MinDuration int      `json:"minduration,omitempty"`
	MaxDuration int      `json:"maxduration,omitempty"`
	W           int      `json:"w,omitempty"`
	H           int      `json:"h,omitempty"`
	Pos         *int     `json:"pos,omitempty"`
	Skip        int      `json:"skip,omitempty"` // 1 = skippable
}

type Native struct {
	Request string          `json:"request,omitempty"`
	Ver     string          `json:"ver,omitempty"`
	Ext     json.RawMessage `json:"ext,omitempty"`
}

type Site struct {
	ID        string     `json:"id,omitempty"`
	Domain    string     `json:"domain,omitempty"` // проверяется против global+campaign white/blacklist
	Page      string     `json:"page,omitempty"`
	Cat       []string   `json:"cat,omitempty"`
	Keywords  string     `json:"keywords,omitempty"` // CSV контекстных слов страницы
	Publisher *Publisher `json:"publisher,omitempty"`
	Content   *Content   `json:"content,omitempty"`
}

type App struct {
	ID        string     `json:"id,omitempty"`
	Bundle    string     `json:"bundle,omitempty"`
	Domain    string     `json:"domain,omitempty"`
	Cat       []string   `json:"cat,omitempty"`
	Keywords  string     `json:"keywords,omitempty"`
	Publisher *Publisher `json:"publisher,omitempty"`
	Content   *Content   `json:"content,omitempty"`
}

// Publisher — site.publisher / app.publisher (publisher_allow/block в таргетинге).
type Publisher struct {
	ID     string `json:"id,omitempty"`
	Name   string `json:"name,omitempty"`
	Domain string `json:"domain,omitempty"`
}

// Content — контекст страницы/видео (keywords_allow/block в таргетинге).
type Content struct {
	ID       string   `json:"id,omitempty"`
	Title    string   `json:"title,omitempty"`
	Keywords string   `json:"keywords,omitempty"`
	Cat      []string `json:"cat,omitempty"`
	Language string   `json:"language,omitempty"`
}

type Device struct {
	UA             string `json:"ua,omitempty"`
	IP             string `json:"ip,omitempty"`
	IPv6           string `json:"ipv6,omitempty"`
	Geo            *Geo   `json:"geo,omitempty"`
	Make           string `json:"make,omitempty"`  // производитель (Apple, Samsung) — таргетинг device_makes
	Model          string `json:"model,omitempty"` // модель
	OS             string `json:"os,omitempty"`
	OSV            string `json:"osv,omitempty"`
	DeviceType     int    `json:"devicetype,omitempty"`
	IFA            string `json:"ifa,omitempty"` // device id — вход для freq caps и audiences
	Language       string `json:"language,omitempty"`
	ConnectionType int    `json:"connectiontype,omitempty"`
	Carrier        string `json:"carrier,omitempty"`
	DNT            int    `json:"dnt,omitempty"`
	LMT            int    `json:"lmt,omitempty"`
}

type Geo struct {
	Country string  `json:"country,omitempty"` // ISO-3166-1 alpha-3 в OpenRTB; Python нормализует к alpha-2
	Region  string  `json:"region,omitempty"`
	City    string  `json:"city,omitempty"`
	Lat     float64 `json:"lat,omitempty"`
	Lon     float64 `json:"lon,omitempty"`
}

// User — включая user.eids (UID2 / ID5 / RampID): first-class сигнал
// матчинга аудиторий в Go-биддере (рыночное требование, см. go_bidder_plan.md §eids).
type User struct {
	ID       string `json:"id,omitempty"`
	BuyerUID string `json:"buyeruid,omitempty"`
	EIDs     []EID  `json:"eids,omitempty"`
	Gender   string `json:"gender,omitempty"` // M / F / O
	YOB      int    `json:"yob,omitempty"`    // год рождения
}

// EID — extended identifier (OpenRTB 2.5 user.ext.eids / 2.6 user.eids).
type EID struct {
	Source string `json:"source"` // напр. "uidapi.com" (UID2), "id5-sync.com"
	UIDs   []UID  `json:"uids"`
}

type UID struct {
	ID    string          `json:"id"`
	AType int             `json:"atype,omitempty"`
	Ext   json.RawMessage `json:"ext,omitempty"`
}

// --- Response ---

type BidResponse struct {
	ID      string    `json:"id"`
	SeatBid []SeatBid `json:"seatbid,omitempty"`
	Cur     string    `json:"cur,omitempty"` // всегда "RUB"
	NBR     *int      `json:"nbr,omitempty"`
}

type SeatBid struct {
	Bid  []Bid  `json:"bid"`
	Seat string `json:"seat,omitempty"`
}

type Bid struct {
	ID      string          `json:"id"`
	ImpID   string          `json:"impid"`
	Price   float64         `json:"price"`         // сериализация из Decimal-вычислений, 4 знака
	AdM     string          `json:"adm,omitempty"` // креатив: banner HTML с erid-маркировкой / VAST / native JSON
	ADomain []string        `json:"adomain,omitempty"`
	CrID    string          `json:"crid,omitempty"`
	NURL    string          `json:"nurl,omitempty"` // win notice -> /rtb/win
	LURL    string          `json:"lurl,omitempty"` // loss notice -> /rtb/loss
	W       int             `json:"w,omitempty"`
	H       int             `json:"h,omitempty"`
	DealID  string          `json:"dealid,omitempty"` // PMP: id сделки из imp.pmp.deals, если line item ограничен deal_ids
	Ext     json.RawMessage `json:"ext,omitempty"`    // ext.erid — ОРД-маркировка (обязательна для РФ)
}
