package targeting

import "testing"

func TestMatchDomainBlacklistWins(t *testing.T) {
	if MatchDomain("bad.example.com", []string{"example.com"}, []string{"bad.example.com"}) {
		t.Fatal("blacklist must win over whitelist")
	}
}

func TestMatchDomainSuffix(t *testing.T) {
	if !MatchDomain("sub.example.com", []string{"example.com"}, nil) {
		t.Fatal("subdomain must match whitelist suffix")
	}
	if MatchDomain("notexample.com", []string{"example.com"}, nil) {
		t.Fatal("notexample.com must NOT match example.com (no substring match)")
	}
}

func TestMatchDomainEmptyWhitelistAllows(t *testing.T) {
	if !MatchDomain("anything.ru", nil, nil) {
		t.Fatal("empty lists must allow")
	}
}

func TestMatchDomainEmptyDomain(t *testing.T) {
	if !MatchDomain("", nil, nil) {
		t.Fatal("empty domain with no whitelist must pass")
	}
	if MatchDomain("", []string{"example.com"}, nil) {
		t.Fatal("empty domain with whitelist must fail")
	}
}

func TestMatchGeo(t *testing.T) {
	if !MatchGeo("RU", []string{"ru", "KZ"}) {
		t.Fatal("case-insensitive geo match expected")
	}
	if MatchGeo("US", []string{"RU"}) {
		t.Fatal("US must not match RU")
	}
	if !MatchGeo("US", nil) {
		t.Fatal("empty targets = allow all")
	}
}
