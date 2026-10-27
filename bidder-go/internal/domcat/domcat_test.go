package domcat

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestNormalize(t *testing.T) {
	cases := map[string]string{
		"WWW.News.RU.": "news.ru", "news.ru": "news.ru", " m.site.ru ": "m.site.ru",
		"": "", "com.example.app": "com.example.app", "bundle": "", "www.": "",
	}
	for in, want := range cases {
		if got := Normalize(in); got != want {
			t.Errorf("Normalize(%q)=%q want %q", in, got, want)
		}
	}
}

func TestMapSetLookup(t *testing.T) {
	var m Map
	if m.Lookup("a.ru") != nil || m.Len() != 0 {
		t.Fatal("empty map must return nil")
	}
	var nilMap *Map
	if nilMap.Lookup("a.ru") != nil {
		t.Fatal("nil map must be safe")
	}
	m.Set(&Payload{Domains: map[string][]string{
		"WWW.A.ru": {"iab1", "IAB2-3"}, "empty.ru": {}, "bad": {"IAB1"},
	}})
	got := m.Lookup("a.ru")
	if len(got) != 2 || got[0] != "IAB1" || got[1] != "IAB2-3" {
		t.Fatalf("got %v", got)
	}
	if m.Len() != 1 {
		t.Fatalf("only valid entries must remain, len=%d", m.Len())
	}
}

func TestLoadErrorKeepsPreviousMap(t *testing.T) {
	var m Map
	ok := func(_ context.Context, _ string, out any) error {
		*(out.(*Payload)) = Payload{Domains: map[string][]string{"a.ru": {"IAB1"}}}
		return nil
	}
	if err := m.Load(context.Background(), ok); err != nil {
		t.Fatal(err)
	}
	fail := func(context.Context, string, any) error { return errors.New("backend down") }
	if err := m.Load(context.Background(), fail); err == nil {
		t.Fatal("expected error")
	}
	if len(m.Lookup("a.ru")) != 1 {
		t.Fatal("previous map must survive a failed refresh")
	}
}

func TestGateOncePerTTLAndEviction(t *testing.T) {
	g := newGate(time.Hour, learnShards*2)
	now := time.Now()
	if !g.allow("a.ru", now) || g.allow("a.ru", now.Add(59*time.Minute)) {
		t.Fatal("second call within ttl must be refused")
	}
	if !g.allow("a.ru", now.Add(61*time.Minute)) {
		t.Fatal("after ttl must be allowed again")
	}
	g.forget("a.ru")
	if !g.allow("a.ru", now.Add(62*time.Minute)) {
		t.Fatal("forget must reopen the gate")
	}
	for i := 0; i < 5000; i++ {
		g.allow("d"+string(rune('a'+i%26))+time.Duration(i).String()+".ru", now)
	}
	if g.size() > learnShards*2 {
		t.Fatalf("gate must stay bounded, size=%d", g.size())
	}
}

type fakeWriter struct {
	calls atomic.Int64
	err   error
	delay time.Duration
}

func (f *fakeWriter) Write(ctx context.Context, _ string, _ []string) error {
	f.calls.Add(1)
	select {
	case <-time.After(f.delay):
	case <-ctx.Done():
		return ctx.Err()
	}
	return f.err
}

func TestObserveWritesOncePerDomainAndNeverBlocks(t *testing.T) {
	w := &fakeWriter{delay: 300 * time.Millisecond}
	l := NewLearner(w, time.Hour, 1000)
	start := time.Now()
	for i := 0; i < 1000; i++ {
		l.Observe("same.ru", []string{"IAB1"})
	}
	if time.Since(start) > 50*time.Millisecond {
		t.Fatalf("Observe must not block, took %v", time.Since(start))
	}
	l.Observe("", []string{"IAB1"})
	l.Observe("x.ru", nil)
	var nilL *Learner
	nilL.Observe("x.ru", []string{"IAB1"})
	time.Sleep(50 * time.Millisecond)
	if n := w.calls.Load(); n != 1 {
		t.Fatalf("gate must allow one write per domain, got %d", n)
	}
}

func TestObserveUnreachableRedisDoesNotBlockOrPanic(t *testing.T) {
	cli := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1", DialTimeout: 50 * time.Millisecond, MaxRetries: -1})
	defer cli.Close()
	l := NewLearner(RedisWriter{Client: cli}, time.Hour, 1000)
	start := time.Now()
	l.Observe("down.ru", []string{"IAB1", "IAB2"})
	if time.Since(start) > 20*time.Millisecond {
		t.Fatalf("Observe blocked for %v", time.Since(start))
	}
	deadline := time.Now().Add(2 * time.Second)
	for l.Errors() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if l.Errors() == 0 {
		t.Fatal("write failure must be counted, not returned")
	}
	if !l.gate.allow("down.ru", time.Now()) {
		t.Fatal("failed write must reopen the gate for retry")
	}
}

func TestObserveCapsCategories(t *testing.T) {
	var got atomic.Value
	w := writerFunc(func(_ context.Context, _ string, cats []string) error { got.Store(cats); return nil })
	l := NewLearner(w, time.Hour, 100)
	cats := make([]string, 20)
	for i := range cats {
		cats[i] = "IAB1"
	}
	l.Observe("cap.ru", cats)
	time.Sleep(50 * time.Millisecond)
	if v, _ := got.Load().([]string); len(v) != learnMaxCats {
		t.Fatalf("categories must be capped, got %d", len(v))
	}
}

type writerFunc func(context.Context, string, []string) error

func (f writerFunc) Write(ctx context.Context, d string, c []string) error { return f(ctx, d, c) }
