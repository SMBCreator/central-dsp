"""Контракты Redis между Go-биддером и Python-бэкендом.

1. Стрим `rtb:events`: Go пишет (handler.go, EmitEvent), events_consumer.normalize
   читает и кладёт в bid_events. Поле, которое Go пишет, а consumer не читает —
   потеряно для отчётов; поле, которое consumer читает, а Go не пишет — всегда NULL.
2. Ключ `bidmeta:{bid_id}`: Go пишет JSON при биде, Python читает в win/imp/click
   (rtb.py `_find_bid` -> `row[...]`), Go читает при loss. Отсутствующее поле в
   bidmeta = KeyError в win-notice = потерянный биллинг.

Тесты парсят Go-исходник (не компилируют): дрейф валит CI с именем поля.
"""

from __future__ import annotations

import re
from pathlib import Path

from events_consumer import _INSERT_COLS, normalize

REPO = Path(__file__).resolve().parents[2]
GO_HANDLER = (REPO / "bidder-go" / "internal" / "rtb" / "handler.go").read_text(encoding="utf-8")
PY_RTB = (REPO / "backend" / "routers" / "rtb.py").read_text(encoding="utf-8")
PY_CONSUMER = (REPO / "backend" / "events_consumer.py").read_text(encoding="utf-8")

_KEY_RE = re.compile(r'"([a-z_]+)":\s')


def _go_map_literal_keys(anchor: str) -> set[str]:
    """Ключи map[string]any{...}, начинающегося сразу после `anchor`."""
    i = GO_HANDLER.index(anchor)
    start = GO_HANDLER.index("{", i)
    depth, j = 0, start
    while True:
        ch = GO_HANDLER[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return set(_KEY_RE.findall(GO_HANDLER[start : j + 1]))


GO_BID_EVENT = _go_map_literal_keys("ev := map[string]any")
GO_LOSS_EVENT = _go_map_literal_keys('h.redis.EmitEvent(ctx, map[string]any{\n\t\t\t\t\t"type": "loss"')
GO_BIDMETA = _go_map_literal_keys("meta, _ := json.Marshal(map[string]any")

CONSUMER_READS = set(re.findall(r'fields\.get\("([a-z_]+)"', PY_CONSUMER)) | {"campaign_id", "line_item_id", "creative_id", "supply_partner_id"}
STREAM_META = {"type", "ts"}
CONSUMER_INFRA = {"is_test"}

WIN_NOTICE_READS = set(
    re.findall(r'row\[\"([a-z_]+)\"\]', PY_RTB[PY_RTB.index("async def win_notice") : PY_RTB.index("async def win_notice") + 6000])
)


def test_go_bid_event_fields_are_all_consumed():
    unread = GO_BID_EVENT - CONSUMER_READS
    assert not unread, f"Go emits bid-event fields the consumer drops: {sorted(unread)}"


def test_consumer_reads_only_fields_go_emits_on_bid():
    """Поля, которых нет в bid-событии, всегда NULL в bid_events для bid-строк.
    loss_reason/min_to_win легитимно есть только у loss."""
    never_written = CONSUMER_READS - GO_BID_EVENT - {"loss_reason", "min_to_win"}
    assert not never_written, f"consumer reads fields Go never emits on bid: {sorted(never_written)}"


def test_go_loss_event_is_subset_of_bid_event_plus_loss_fields():
    extra = GO_LOSS_EVENT - GO_BID_EVENT - {"loss_reason", "min_to_win"}
    assert not extra, f"loss event has fields unknown to the bid event/consumer: {sorted(extra)}"
    assert {"type", "bid_id", "loss_reason", "min_to_win", "is_test", "ts"} <= GO_LOSS_EVENT


def test_every_consumed_field_lands_in_a_bid_events_column():
    """normalize() -> колонки bid_events. Поле без колонки — молча выброшено."""
    sample = {k: "x" for k in GO_BID_EVENT}
    sample.update({"type": "bid", "bid_id": "b1", "price": "1.5", "bidfloor": "0.1", "ts": "1700000000", "is_test": "0"})
    row = normalize(sample)
    assert row is not None
    assert set(row) == set(_INSERT_COLS), {
        "row_without_column": sorted(set(row) - set(_INSERT_COLS)),
        "column_without_value": sorted(set(_INSERT_COLS) - set(row)),
    }


def test_stream_values_are_strings_in_go():
    """XREADGROUP отдаёт строки; числа Go обязан форматировать сам, иначе
    go-redis сериализует float как "1.5e+00". Проверяем, что price/bidfloor/
    is_test/ts в bid-событии проходят через strconv."""
    i = GO_HANDLER.index("ev := map[string]any")
    block = GO_HANDLER[i : GO_HANDLER.index("}", i)]
    for f in ("price", "bidfloor", "is_test", "ts"):
        line = next(ln for ln in block.splitlines() if f'"{f}":' in ln)
        assert "strconv." in line, f"{f} is not string-formatted in Go bid event: {line.strip()}"


# --- bidmeta -------------------------------------------------------------------


def test_bidmeta_has_every_field_win_notice_reads():
    missing = WIN_NOTICE_READS - GO_BIDMETA
    assert not missing, f"win_notice reads row[...] keys Go does not put in bidmeta: {sorted(missing)}"


def test_bidmeta_is_test_flag_present():
    """test=1 (OpenRTB) не должен списывать деньги — win_notice читает is_test из bidmeta."""
    assert "is_test" in GO_BIDMETA
    assert "_is_test_bid(row)" in PY_RTB


# --- Lua budget gate ----------------------------------------------------------


GO_REDISX = (REPO / "bidder-go" / "internal" / "redisx" / "redisx.go").read_text(encoding="utf-8")


def _go_lua(const_name: str) -> str:
    m = re.search(rf"const {const_name} = `(.*?)`", GO_REDISX, re.DOTALL)
    assert m, f"{const_name} not found in redisx.go"
    return m.group(1)


def _norm(s: str) -> str:
    return "\n".join(ln.rstrip() for ln in s.strip().splitlines())


def test_gate_lua_is_byte_identical_between_python_and_go():
    """spend:camp/spend:li/avail:*/res:* — общие ключи двух биддеров. Один и
    тот же Lua гарантирует одинаковую семантику лимитов/TTL/отката; правка
    одной стороны без другой = расхождение в деньгах."""
    from cache import _GATE_LUA

    assert _norm(_GATE_LUA) == _norm(_go_lua("luaGateAndReserve"))


def test_release_and_settle_lua_are_byte_identical():
    """Go делает loss/expire (luaReleaseBid), Python — win (luaSettleWin);
    оба скрипта должны совпадать, иначе терминальные состояния бида и возвраты
    резерва расходятся между биддером и бэкендом (audit M-06)."""
    from cache import _RELEASE_BID_LUA, _SETTLE_WIN_LUA

    assert _norm(_RELEASE_BID_LUA) == _norm(_go_lua("luaReleaseBid"))
    assert _norm(_SETTLE_WIN_LUA) == _norm(_go_lua("luaSettleWin"))


def test_confirm_imp_lua_is_byte_identical():
    """Python подтверждает imp (luaConfirmImp) в production, Go держит порт для
    тестов паритета; коды возврата (0/1/3 — late) читают обе стороны."""
    from cache import _CONFIRM_IMP_LUA

    assert _norm(_CONFIRM_IMP_LUA) == _norm(_go_lua("luaConfirmImp"))


def test_reservation_constants_match_go():
    """TTL/ключи резерва: sweeper (Go) и settle (Python) смотрят на одни значения."""
    from cache import BID_STATE_TTL, DEFAULT_RESERVE_TTL, PENDING_BIDS_KEY

    assert f'const PendingBidsKey = "{PENDING_BIDS_KEY}"' in GO_REDISX
    m = re.search(r"const DefaultReserveTTL = (\d+) \* time\.Second", GO_REDISX)
    assert m and int(m.group(1)) == DEFAULT_RESERVE_TTL
    m = re.search(r"const BidStateTTL = (\d+) \* time\.Hour", GO_REDISX)
    assert m and int(m.group(1)) * 3600 == BID_STATE_TTL
    # WIN_IMP_TTL: одинаковый дефолт и одна и та же env-переменная на обеих сторонах.
    from cache import DEFAULT_WIN_IMP_TTL

    m = re.search(r"const DefaultWinImpTTL = (\d+) \* time\.Second", GO_REDISX)
    assert m and int(m.group(1)) == DEFAULT_WIN_IMP_TTL
    assert 'os.Getenv("WIN_IMP_TTL")' in GO_REDISX


def test_bidmeta_carries_reservation_fields():
    """ParseReservation (Go sweeper/loss) и reservation_from_bidmeta (Python win)
    читают одни поля; оба биддера обязаны их писать."""
    needed = {"advertiser_id", "reserve", "business_date", "has_total", "has_li", "is_test"}
    assert needed <= GO_BIDMETA, f"Go bidmeta lacks: {sorted(needed - GO_BIDMETA)}"
    i = PY_RTB.index("reserve_meta = {")
    block = PY_RTB[i : PY_RTB.index("}", i)]
    py_meta = set(re.findall(r'"([a-z_]+)":', block))
    assert needed - {"is_test"} <= py_meta, f"Python reserve_meta lacks: {sorted(needed - {'is_test'} - py_meta)}"


def test_python_bidmeta_writer_matches_go_bidmeta():
    """Python-биддер (legacy rtb.py) тоже пишет bidmeta — тот же набор ключей,
    иначе win-notice поведение зависит от того, какой биддер ответил."""
    i = PY_RTB.index('f"bidmeta:{bid_id}",')
    block = PY_RTB[i : PY_RTB.index("ex=", i)]
    py_keys = set(re.findall(r'"([a-z_]+)":', block))
    assert WIN_NOTICE_READS <= py_keys, f"Python bidmeta lacks: {sorted(WIN_NOTICE_READS - py_keys)}"
    assert py_keys - GO_BIDMETA == set(), f"Python bidmeta has keys Go lacks: {sorted(py_keys - GO_BIDMETA)}"
