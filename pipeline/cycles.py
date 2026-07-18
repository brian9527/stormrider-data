from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CYCLE_HOURS = (0, 6, 12, 18)
PUBLICATION_LAG = timedelta(hours=4, minutes=30)
FORECAST_HOURS = tuple(range(0, 49, 3))  # f000..f048, 17 frames


@dataclass(frozen=True)
class Cycle:
    run: datetime  # timezone-aware UTC

    @property
    def date_str(self) -> str:
        return self.run.strftime("%Y%m%d")

    @property
    def hour_str(self) -> str:
        return f"{self.run.hour:02d}"

    @property
    def cycle_id(self) -> str:
        return f"{self.date_str}T{self.hour_str}"

    def valid_time(self, forecast_hour: int) -> datetime:
        return self.run + timedelta(hours=forecast_hour)


def candidate_cycles(now: datetime, max_fallback: int = 2) -> list[Cycle]:
    """由新到舊列出「發布 lag 已過」的輪次，共 max_fallback+1 個。"""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    latest_possible = now.astimezone(timezone.utc) - PUBLICATION_LAG
    day = latest_possible.date()
    cycles: list[Cycle] = []
    while len(cycles) < max_fallback + 1:
        for hour in sorted(CYCLE_HOURS, reverse=True):
            run = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if run <= latest_possible and len(cycles) < max_fallback + 1:
                cycles.append(Cycle(run))
        day -= timedelta(days=1)
    return cycles
