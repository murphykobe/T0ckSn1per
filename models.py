"""Shared data models."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

RESERVATION_TIME_FORMAT = "%I:%M %p"

MONTH_NUM = {
    "january": "01",  "february": "02", "march": "03",    "april": "04",
    "may": "05",      "june": "06",     "july": "07",     "august": "08",
    "september": "09","october": "10",  "november": "11", "december": "12",
}


@dataclass
class Task:
    """
    A single restaurant reservation search target.

    Fields
    ------
    url           : Tock restaurant slug, e.g. "taneda"
    size          : Party size as a string, e.g. "2"
    year          : 4-digit year, e.g. "2026"
    month         : Full month name, e.g. "March"
    days          : Zero-padded day strings to watch, e.g. ["01", "14", "28"]
    earliest_time : Lower bound of acceptable window, e.g. "5:00 PM"
    latest_time   : Upper bound of acceptable window, e.g. "9:30 PM"
    """

    url:            str
    size:           str
    year:           str
    month:          str
    days:           List[str]
    earliest_time:  str
    latest_time:    str

    def formatted_earliest(self) -> datetime:
        return datetime.strptime(self.earliest_time, RESERVATION_TIME_FORMAT)

    def formatted_latest(self) -> datetime:
        return datetime.strptime(self.latest_time, RESERVATION_TIME_FORMAT)

    def search_url(self) -> str:
        month_n = MONTH_NUM[self.month.lower()]
        return (
            f"https://www.exploretock.com/{self.url}/search"
            f"?date={self.year}-{month_n}-01&size={self.size}&time=19%3A00"
        )

    def to_dict(self) -> dict:
        return {
            "url":           self.url,
            "size":          self.size,
            "year":          self.year,
            "month":         self.month,
            "days":          self.days,
            "earliest_time": self.earliest_time,
            "latest_time":   self.latest_time,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(**d)

    def __repr__(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
