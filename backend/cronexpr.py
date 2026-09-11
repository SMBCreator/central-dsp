"""Минимальный парсер 5-полевых cron-выражений (audit O-01, C1b).

Поддерживается классический синтаксис: `*`, число, `a-b`, `*/n`, `a-b/n`,
списки через запятую; воскресенье = 0 или 7. Имена месяцев/дней и макросы
(`@daily`) намеренно не поддерживаются — расписание хранится в БД и
редактируется админом, единый строгий формат проще проверять.

Семантика day-of-month / day-of-week — как у Vixie cron: если ограничены оба
поля, срабатывает совпадение ЛЮБОГО из них; если одно из них `*`, оно не
участвует. Время — UTC (naive datetime не принимается).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("dow", 0, 6),
)


class CronError(ValueError):
    pass


def _parse_field(text: str, name: str, lo: int, hi: int) -> tuple[frozenset[int], bool]:
    """-> (набор значений, является ли поле «звёздочкой» без ограничений)."""
    if not text:
        raise CronError(f"{name}: empty field")
    values: set[int] = set()
    unrestricted = False
    for part in text.split(","):
        step = 1
        rng = part
        if "/" in part:
            rng, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError(f"{name}: bad step {part!r}")
            step = int(step_s)
        if rng == "*":
            start, end = lo, hi
            if step == 1:
                unrestricted = True
        elif "-" in rng:
            a, b = rng.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise CronError(f"{name}: bad range {part!r}")
            start, end = int(a), int(b)
        else:
            if not rng.isdigit():
                raise CronError(f"{name}: bad value {part!r}")
            start = end = int(rng)
            if step != 1:
                # `5/10` в Vixie cron = 5-hi/10
                end = hi
        hi_eff = 7 if name == "dow" else hi  # 7 = воскресенье (алиас 0)
        if start < lo or end > hi_eff or start > end:
            raise CronError(f"{name}: value out of range in {part!r} ({lo}-{hi_eff})")
        for v in range(start, end + 1, step):
            values.add(v % 7 if name == "dow" else v)
    return frozenset(values), unrestricted


@dataclass(frozen=True)
class CronExpr:
    text: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    dows: frozenset[int]
    day_any: bool
    dow_any: bool

    @classmethod
    def parse(cls, text: str) -> CronExpr:
        parts = text.split()
        if len(parts) != 5:
            raise CronError(f"expected 5 fields, got {len(parts)}: {text!r}")
        parsed = [_parse_field(p, n, lo, hi) for p, (n, lo, hi) in zip(parts, _FIELDS, strict=True)]
        return cls(
            text=" ".join(parts),
            minutes=parsed[0][0],
            hours=parsed[1][0],
            days=parsed[2][0],
            months=parsed[3][0],
            dows=parsed[4][0],
            day_any=parsed[2][1],
            dow_any=parsed[4][1],
        )

    def _day_matches(self, d: datetime) -> bool:
        dom = d.day in self.days
        # cron: Monday=1..Sunday=0; python weekday(): Monday=0..Sunday=6
        dow = ((d.weekday() + 1) % 7) in self.dows
        if self.day_any and self.dow_any:
            return True
        if self.day_any:
            return dow
        if self.dow_any:
            return dom
        return dom or dow

    def matches(self, d: datetime) -> bool:
        return (
            d.month in self.months
            and self._day_matches(d)
            and d.hour in self.hours
            and d.minute in self.minutes
        )

    def next_after(self, after: datetime) -> datetime:
        """Ближайший момент строго ПОСЛЕ `after` (aware UTC), секунды обнулены.
        Горизонт — 4 года (достаточно для 29 февраля); дальше — CronError."""
        if after.tzinfo is None:
            raise CronError("naive datetime")
        t = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = t + timedelta(days=366 * 4)
        while t < limit:
            if t.month not in self.months:
                # к первому числу следующего месяца
                y, m = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
                t = t.replace(year=y, month=m, day=1, hour=0, minute=0)
                continue
            if not self._day_matches(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if t.hour not in self.hours:
                t = (t + timedelta(hours=1)).replace(minute=0)
                continue
            if t.minute not in self.minutes:
                t += timedelta(minutes=1)
                continue
            return t
        raise CronError(f"no occurrence within 4 years: {self.text!r}")


def validate(text: str) -> str:
    """Нормализованное выражение или CronError."""
    return CronExpr.parse(text).text


def next_run(text: str, after: datetime) -> datetime:
    return CronExpr.parse(text).next_after(after)
