package shading

import "testing"

func TestShouldShadeFirstPriceOnly(t *testing.T) {
	if !ShouldShade(true, 1) {
		t.Fatal("enabled + first price must shade")
	}
	if ShouldShade(true, 2) {
		t.Fatal("second price must never shade")
	}
	if ShouldShade(false, 1) {
		t.Fatal("disabled campaign must not shade")
	}
	if ShouldShade(true, 0) || ShouldShade(true, 3) || ShouldShade(true, 500) {
		t.Fatal("unknown auction types must not shade")
	}
}
