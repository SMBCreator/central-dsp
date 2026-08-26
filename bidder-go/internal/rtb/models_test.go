package rtb

import (
	"encoding/json"
	"testing"
)

func TestBidRequestParseEIDs(t *testing.T) {
	raw := `{
		"id": "req-1", "at": 1,
		"imp": [{"id": "1", "banner": {"w": 300, "h": 250}, "bidfloor": 12.34, "bidfloorcur": "RUB"}],
		"site": {"domain": "news.example.ru"},
		"device": {"ifa": "abc-123", "geo": {"country": "RUS"}},
		"user": {"id": "u1", "eids": [
			{"source": "uidapi.com", "uids": [{"id": "UID2-token", "atype": 3}]},
			{"source": "id5-sync.com", "uids": [{"id": "ID5-token"}]}
		]}
	}`
	var req BidRequest
	if err := json.Unmarshal([]byte(raw), &req); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if req.At != 1 {
		t.Fatalf("at: want 1, got %d", req.At)
	}
	if len(req.User.EIDs) != 2 || req.User.EIDs[0].Source != "uidapi.com" {
		t.Fatalf("eids parsed wrong: %+v", req.User.EIDs)
	}
	if req.Device.Geo.Country != "RUS" {
		t.Fatalf("geo country: %q", req.Device.Geo.Country)
	}
}

func TestBidResponseRoundTrip(t *testing.T) {
	resp := BidResponse{
		ID:  "req-1",
		Cur: "RUB",
		SeatBid: []SeatBid{{Bid: []Bid{{
			ID: "b1", ImpID: "1", Price: 42.1234,
			Ext: json.RawMessage(`{"erid":"2VtzqvQZq1x"}`),
		}}}},
	}
	b, err := json.Marshal(resp)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var back BidResponse
	if err := json.Unmarshal(b, &back); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if back.Cur != "RUB" || back.SeatBid[0].Bid[0].Price != 42.1234 {
		t.Fatalf("roundtrip mismatch: %+v", back)
	}
}
