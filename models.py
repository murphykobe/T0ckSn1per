"""Shared data models."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

RESERVATION_TIME_FORMAT = "%I:%M %p"


@dataclass
class Target:
    """
    A single date/time window to watch for a reservation.

    Fields
    ------
    date          : ISO date string, e.g. "2026-03-15"
    earliest_time : Lower bound of acceptable window, e.g. "5:00 PM"
    latest_time   : Upper bound of acceptable window, e.g. "9:30 PM"
    """

    date:           str
    earliest_time:  str
    latest_time:    str

    def formatted_earliest(self) -> datetime:
        return datetime.strptime(self.earliest_time, RESERVATION_TIME_FORMAT)

    def formatted_latest(self) -> datetime:
        return datetime.strptime(self.latest_time, RESERVATION_TIME_FORMAT)

    def search_url(self, restaurant_slug: str, party_size: str) -> str:
        return (
            f"https://www.exploretock.com/{restaurant_slug}/search"
            f"?date={self.date}&size={party_size}&time=19%3A00"
        )

    def to_dict(self) -> dict:
        return {
            "date":           self.date,
            "earliest_time":  self.earliest_time,
            "latest_time":    self.latest_time,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Target":
        return cls(**d)


@dataclass
class Task:
    """
    A restaurant reservation sniper task.

    Fields
    ------
    url     : Tock restaurant slug, e.g. "alinea"
    size    : Party size as a string, e.g. "2"
    targets : List of Target objects specifying date/time windows to watch
    """

    url:     str
    size:    str
    targets: List[Target] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "url":     self.url,
            "size":    self.size,
            "targets": [t.to_dict() for t in self.targets],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        targets = [Target.from_dict(t) for t in d.get("targets", [])]
        return cls(url=d["url"], size=d["size"], targets=targets)

    def __repr__(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
