package domcat

import (
	"container/list"
	"context"
	"hash/fnv"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	// LearnKey — хэш Redis, который забирает backend (/cron/domcat-learn).
	// Поля: "<domain>" — число наблюдений, "<domain>|<IAB>" — число раз категория.
	LearnKey = "domcat:learn"

	learnTTL       = 30 * 24 * time.Hour
	learnTimeout   = 200 * time.Millisecond
	learnMaxCats   = 8
	learnInflight  = 64
	learnShards    = 16
	DefaultGateTTL = time.Hour
	DefaultGateCap = 100_000
)

// Writer — запись наблюдения в хранилище. Реализация для Redis — RedisWriter.
type Writer interface {
	Write(ctx context.Context, domain string, cats []string) error
}

// RedisWriter пишет наблюдения в хэш LearnKey одним pipeline-запросом.
type RedisWriter struct{ Client redis.Cmdable }

func (w RedisWriter) Write(ctx context.Context, domain string, cats []string) error {
	pipe := w.Client.Pipeline()
	pipe.HIncrBy(ctx, LearnKey, domain, 1)
	for _, c := range cats {
		pipe.HIncrBy(ctx, LearnKey, domain+"|"+c, 1)
	}
	pipe.Expire(ctx, LearnKey, learnTTL)
	_, err := pipe.Exec(ctx)
	return err
}

// Learner — асинхронное самообучение: не чаще раза в gateTTL на домен и не
// блокирует ответ. Любая ошибка записи (в т.ч. недоступный Redis) только
// считается и логируется с ограничением частоты, наружу не уходит.
type Learner struct {
	w      Writer
	gate   *gate
	sem    chan struct{}
	errs   atomic.Int64
	drops  atomic.Int64
	lastWr atomic.Int64 // unixnano последнего предупреждения в лог
}

// NewLearner: w — приёмник записей; gateTTL/gateCap — гейт «раз в час на домен»
// с ограничением размера (при переполнении вытесняются самые давние домены).
func NewLearner(w Writer, gateTTL time.Duration, gateCap int) *Learner {
	if gateTTL <= 0 {
		gateTTL = DefaultGateTTL
	}
	if gateCap <= 0 {
		gateCap = DefaultGateCap
	}
	return &Learner{w: w, gate: newGate(gateTTL, gateCap), sem: make(chan struct{}, learnInflight)}
}

// Observe фиксирует наблюдение «у домена есть категории cats» (cats — те, что
// прислала SSP). Возвращает сразу; запись выполняется в отдельной горутине.
func (l *Learner) Observe(domain string, cats []string) {
	if l == nil || l.w == nil || domain == "" || len(cats) == 0 {
		return
	}
	if !l.gate.allow(domain, time.Now()) {
		return
	}
	select {
	case l.sem <- struct{}{}:
	default:
		l.drops.Add(1)
		l.gate.forget(domain)
		return
	}
	if len(cats) > learnMaxCats {
		cats = cats[:learnMaxCats]
	}
	cp := append([]string(nil), cats...)
	go func() {
		defer func() {
			<-l.sem
			if r := recover(); r != nil {
				l.errs.Add(1)
			}
		}()
		ctx, cancel := context.WithTimeout(context.Background(), learnTimeout)
		defer cancel()
		if err := l.w.Write(ctx, domain, cp); err != nil {
			l.errs.Add(1)
			l.gate.forget(domain) // повторить при следующем запросе, а не через час
			l.warn(err)
		}
	}()
}

// Errors / Drops — счётчики для диагностики и тестов.
func (l *Learner) Errors() int64 { return l.errs.Load() }
func (l *Learner) Drops() int64  { return l.drops.Load() }

func (l *Learner) warn(err error) {
	now := time.Now().UnixNano()
	last := l.lastWr.Load()
	if now-last < int64(time.Minute) || !l.lastWr.CompareAndSwap(last, now) {
		return
	}
	slog.Warn("domain category learning write failed", "err", err)
}

// gate — шардированный LRU «домен → время последней записи».
type gate struct {
	ttl    time.Duration
	shards [learnShards]gateShard
}

type gateShard struct {
	mu  sync.Mutex
	cap int
	ll  *list.List
	idx map[string]*list.Element
}

type gateEntry struct {
	domain string
	at     time.Time
}

func newGate(ttl time.Duration, capacity int) *gate {
	g := &gate{ttl: ttl}
	per := capacity / learnShards
	if per < 1 {
		per = 1
	}
	for i := range g.shards {
		g.shards[i] = gateShard{cap: per, ll: list.New(), idx: make(map[string]*list.Element, 64)}
	}
	return g
}

func (g *gate) shard(domain string) *gateShard {
	h := fnv.New32a()
	_, _ = h.Write([]byte(domain))
	return &g.shards[h.Sum32()%learnShards]
}

// allow: true, если по домену давно не писали; тогда время фиксируется.
func (g *gate) allow(domain string, now time.Time) bool {
	s := g.shard(domain)
	s.mu.Lock()
	defer s.mu.Unlock()
	if el, ok := s.idx[domain]; ok {
		e := el.Value.(*gateEntry)
		if now.Sub(e.at) < g.ttl {
			s.ll.MoveToFront(el)
			return false
		}
		e.at = now
		s.ll.MoveToFront(el)
		return true
	}
	s.idx[domain] = s.ll.PushFront(&gateEntry{domain: domain, at: now})
	for s.ll.Len() > s.cap {
		old := s.ll.Back()
		s.ll.Remove(old)
		delete(s.idx, old.Value.(*gateEntry).domain)
	}
	return true
}

func (g *gate) forget(domain string) {
	s := g.shard(domain)
	s.mu.Lock()
	defer s.mu.Unlock()
	if el, ok := s.idx[domain]; ok {
		s.ll.Remove(el)
		delete(s.idx, domain)
	}
}

func (g *gate) size() int {
	n := 0
	for i := range g.shards {
		g.shards[i].mu.Lock()
		n += g.shards[i].ll.Len()
		g.shards[i].mu.Unlock()
	}
	return n
}
