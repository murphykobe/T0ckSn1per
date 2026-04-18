"""Unit tests for main.py helpers."""

import argparse
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import _build_inline_task, _build_parser


def test_build_inline_task_uses_date_and_exact_time_flags():
    args = argparse.Namespace(
        slug="taneda",
        size="2",
        target=None,
        date=["2026-06-17", "2026-06-18"],
        exact_time=["5:15 PM", "7:45 PM"],
        release_at="11:00",
        newly_released_only=True,
    )

    task = _build_inline_task(args)

    assert task.launch is not None
    assert task.launch.release_at == "11:00"
    assert task.launch.newly_released_only is True
    assert [selector.to_dict() for selector in task.selectors] == [
        {
            "dates": ["2026-06-17", "2026-06-18"],
            "exact_times": ["5:15 PM", "7:45 PM"],
        }
    ]


def test_build_inline_task_preserves_legacy_target_input():
    args = argparse.Namespace(
        slug="canlis",
        size="2",
        target=[["2026-03-15", "5:00 PM", "9:30 PM", "4"]],
        date=[],
        exact_time=[],
        release_at=None,
        newly_released_only=False,
    )

    task = _build_inline_task(args)

    assert task.size == "4"
    assert [selector.to_dict() for selector in task.selectors] == [
        {
            "dates": ["2026-03-15"],
            "earliest_time": "5:00 PM",
            "latest_time": "9:30 PM",
        }
    ]


def test_parser_accepts_plural_comma_separated_lists():
    parser = _build_parser()

    args = parser.parse_args([
        "run",
        "taneda",
        "--size", "3",
        "--dates", "2026-05-27,2026-05-28,2026-05-29",
        "--exact-times", "5:15 PM,7:45 PM",
        "--release-at", "11:00",
        "--newly-released-only",
    ])

    task = _build_inline_task(args)

    assert [selector.to_dict() for selector in task.selectors] == [
        {
            "dates": ["2026-05-27", "2026-05-28", "2026-05-29"],
            "exact_times": ["5:15 PM", "7:45 PM"],
        }
    ]


def test_parser_keeps_legacy_singular_flags_working():
    parser = _build_parser()

    args = parser.parse_args([
        "snipe",
        "taneda",
        "--date", "2026-05-27",
        "--date", "2026-05-28",
        "--exact-time", "5:15 PM",
        "--exact-time", "7:45 PM",
    ])

    task = _build_inline_task(args)

    assert [selector.to_dict() for selector in task.selectors] == [
        {
            "dates": ["2026-05-27", "2026-05-28"],
            "exact_times": ["5:15 PM", "7:45 PM"],
        }
    ]
