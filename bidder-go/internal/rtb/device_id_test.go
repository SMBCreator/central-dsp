package rtb

import "testing"

func TestDeviceIDOf(t *testing.T) {
	const zero = "00000000-0000-0000-0000-000000000000"
	eids := []EID{{Source: "uidapi.com", UIDs: []UID{{ID: "uid2-abc"}}}}
	cases := []struct {
		name string
		req  BidRequest
		want string
	}{
		{"ifa wins", BidRequest{Device: &Device{IFA: "ifa-1"}, User: &User{ID: "u1"}}, "ifa-1"},
		{"zero ifa skipped -> user.id namespaced", BidRequest{Device: &Device{IFA: zero}, User: &User{ID: "u1"}}, "p:sspA:u1"},
		{"zero ifa without dashes skipped", BidRequest{Device: &Device{IFA: "00000000000000000000000000000000"}, User: &User{BuyerUID: "b1"}}, "b1"},
		{"eid before user.id, not namespaced", BidRequest{User: &User{ID: "u1", EIDs: eids}}, "uid2-abc"},
		{"zero eid skipped", BidRequest{User: &User{ID: "u1", EIDs: []EID{{UIDs: []UID{{ID: zero}}}}}}, "p:sspA:u1"},
		{"user.id namespaced by partner", BidRequest{User: &User{ID: "u1"}}, "p:sspA:u1"},
		{"buyeruid global", BidRequest{User: &User{BuyerUID: "b1"}}, "b1"},
		{"only zero ifa -> empty", BidRequest{Device: &Device{IFA: zero}}, ""},
		{"nothing", BidRequest{}, ""},
	}
	for _, c := range cases {
		if got := deviceIDOf(&c.req, "sspA"); got != c.want {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
	// одинаковый user.id у разных SSP — разные пользователи
	r := BidRequest{User: &User{ID: "same"}}
	if deviceIDOf(&r, "a") == deviceIDOf(&r, "b") {
		t.Error("user.id from different partners must not collide")
	}
}
