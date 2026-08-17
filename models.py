"""Shared data models."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

RESERVATION_TIME_FORMAT = "%I:%M %p"


def expand_date_ranges(raw: Optional[str]) -> List[str]:
    if not raw:
        return []

    expanded: List[str] = []
    for part in [piece.strip() for piece in raw.split(",") if piece.strip()]:
        start_s, end_s = [token.strip() for token in part.split(":", 1)]
        start = datetime.strptime(start_s, "%Y-%m-%d").date()
        end = datetime.strptime(end_s, "%Y-%m-%d").date()
        if end < start:
            raise ValueError(f"Invalid date range: {part}")

        cursor = start
        while cursor <= end:
            expanded.append(cursor.isoformat())
            cursor += timedelta(days=1)

    return expanded


@dataclass
class LaunchConfig:
    """Launch-mode settings for timed drops."""

    release_at: str
    newly_released_only: bool = False

    def to_dict(self) -> dict:
        return {
            "release_at": self.release_at,
            "newly_released_only": self.newly_released_only,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LaunchConfig":
        return cls(
            release_at=data["release_at"],
            newly_released_only=data.get("newly_released_only", False),
        )


@dataclass
class Target:
    """
    A concrete date/time attempt to watch for a reservation.

    In exact mode, earliest_time == latest_time and exact_time is populated.
    """

    date: str
    earliest_time: str
    latest_time: str
    exact_time: Optional[str] = None

    def earliest_dt(self) -> datetime:
        return datetime.strptime(self.earliest_time, RESERVATION_TIME_FORMAT)

    def latest_dt(self) -> datetime:
        return datetime.strptime(self.latest_time, RESERVATION_TIME_FORMAT)

    def matches_time(self, time_text: str) -> bool:
        if self.exact_time is not None:
            return time_text == self.exact_time

        try:
            candidate = datetime.strptime(time_text, RESERVATION_TIME_FORMAT)
        except ValueError:
            return False
        return self.earliest_dt() <= candidate <= self.latest_dt()

    def search_url(self, restaurant_slug: str, party_size: str) -> str:
        # Derive time param from midpoint of the target window so Tock's
        # UI scrolls to roughly the right area of the day.
        try:
            e = self.earliest_dt()
            l = self.latest_dt()
            mid_minutes = (e.hour * 60 + e.minute + l.hour * 60 + l.minute) // 2
            mid_h, mid_m = divmod(mid_minutes, 60)
            time_param = f"{mid_h}%3A{mid_m:02d}"
        except (ValueError, TypeError):
            time_param = "19%3A00"
        return (
            f"https://www.exploretock.com/{restaurant_slug}/search"
            f"?date={self.date}&size={party_size}&time={time_param}"
        )

    def to_dict(self) -> dict:
        data = {
            "date": self.date,
            "earliest_time": self.earliest_time,
            "latest_time": self.latest_time,
        }
        if self.exact_time:
            data["exact_time"] = self.exact_time
        return data

    @classmethod
    def from_dict(cls, d: dict) -> "Target":
        return cls(
            date=d["date"],
            earliest_time=d["earliest_time"],
            latest_time=d["latest_time"],
            exact_time=d.get("exact_time"),
        )


@dataclass
class Selector:
    """Compact user intent that can expand into one or more concrete targets."""

    dates: List[str]
    earliest_time: Optional[str] = None
    latest_time: Optional[str] = None
    exact_times: List[str] = field(default_factory=list)

    def expand_targets(self) -> List[Target]:
        if self.exact_times:
            return [
                Target(
                    date=date,
                    earliest_time=exact_time,
                    latest_time=exact_time,
                    exact_time=exact_time,
                )
                for date in self.dates
                for exact_time in self.exact_times
            ]

        earliest_time = self.earliest_time or "12:00 PM"
        latest_time = self.latest_time or "11:00 PM"
        return [
            Target(
                date=date,
                earliest_time=earliest_time,
                latest_time=latest_time,
            )
            for date in self.dates
        ]

    def to_dict(self) -> dict:
        data = {"dates": list(self.dates)}
        if self.exact_times:
            data["exact_times"] = list(self.exact_times)
        else:
            if self.earliest_time is not None:
                data["earliest_time"] = self.earliest_time
            if self.latest_time is not None:
                data["latest_time"] = self.latest_time
        return data

    @classmethod
    def from_dict(cls, d: dict) -> "Selector":
        return cls(
            dates=list(d.get("dates", [])),
            earliest_time=d.get("earliest_time"),
            latest_time=d.get("latest_time"),
            exact_times=list(d.get("exact_times", [])),
        )


@dataclass
class Task:
    """
    A restaurant reservation sniper task.

    `selectors` is the canonical representation. `targets` remains available as
    a computed compatibility property for the existing worker code.
    """

    url: str
    size: str
    selectors: List[Selector] = field(default_factory=list)
    launch: Optional[LaunchConfig] = None

    @property
    def targets(self) -> List[Target]:
        return self.expand_targets()

    def expand_targets(self) -> List[Target]:
        targets: List[Target] = []
        for selector in self.selectors:
            targets.extend(selector.expand_targets())
        return targets

    def filter_dates(self, eligible_dates: List[str]) -> "Task":
        allowed = set(eligible_dates)
        selectors: List[Selector] = []
        for selector in self.selectors:
            source_dates = selector.dates or list(eligible_dates)
            kept_dates = [date for date in source_dates if date in allowed]
            if kept_dates:
                selectors.append(Selector(
                    dates=kept_dates,
                    earliest_time=selector.earliest_time,
                    latest_time=selector.latest_time,
                    exact_times=list(selector.exact_times),
                ))
        return Task(url=self.url, size=self.size, selectors=selectors, launch=self.launch)

    def to_dict(self) -> dict:
        data = {
            "url": self.url,
            "size": self.size,
        }

        if self.launch is not None:
            data["launch"] = self.launch.to_dict()

        can_emit_legacy_targets = (
            self.launch is None and
            all(len(selector.dates) == 1 and not selector.exact_times for selector in self.selectors)
        )

        if can_emit_legacy_targets:
            data["targets"] = [
                {
                    "date": selector.dates[0],
                    "earliest_time": selector.earliest_time or "12:00 PM",
                    "latest_time": selector.latest_time or "11:00 PM",
                }
                for selector in self.selectors
            ]
        else:
            data["selectors"] = [selector.to_dict() for selector in self.selectors]

        return data

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        selectors_data = d.get("selectors")
        if selectors_data is None:
            selectors_data = [
                {
                    "dates": [target["date"]],
                    "earliest_time": target["earliest_time"],
                    "latest_time": target["latest_time"],
                }
                for target in d.get("targets", [])
            ]

        return cls(
            url=d["url"],
            size=d["size"],
            selectors=[Selector.from_dict(selector) for selector in selectors_data],
            launch=LaunchConfig.from_dict(d["launch"]) if d.get("launch") else None,
        )

    def __repr__(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
