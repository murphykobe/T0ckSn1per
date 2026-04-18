"""Unit tests for main.py helpers."""

import argparse
import logging
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as main_module
from main import _build_inline_task, _build_parser, _should_use_inline_task


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


def test_parser_accepts_cdp_url_for_run():
    parser = _build_parser()

    args = parser.parse_args([
        "run",
        "taneda",
        "--size", "3",
        "--dates", "2026-05-27,2026-05-28",
        "--exact-times", "5:15 PM,7:45 PM",
        "--cdp-url", "http://127.0.0.1:9222",
    ])

    assert args.cdp_url == "http://127.0.0.1:9222"


def test_parser_accepts_monitoring_flags_and_date_ranges():
    parser = _build_parser()

    args = parser.parse_args([
        "run",
        "taneda",
        "--size", "1",
        "--monitor",
        "--monitor-duration", "20",
        "--date-ranges", "2026-05-07:2026-05-09",
    ])

    assert args.monitor is True
    assert args.monitor_duration == 20
    assert args.date_ranges == "2026-05-07:2026-05-09"


def test_build_inline_task_expands_date_ranges():
    args = argparse.Namespace(
        slug="taneda",
        size="1",
        target=None,
        date=[],
        dates=None,
        date_ranges="2026-05-07:2026-05-09",
        exact_time=[],
        exact_times=None,
        release_at=None,
        newly_released_only=False,
        monitor=True,
        monitor_duration=15,
    )

    task = _build_inline_task(args)

    assert [selector.to_dict() for selector in task.selectors] == [
        {"dates": ["2026-05-07", "2026-05-08", "2026-05-09"]}
    ]


def test_monitoring_mode_does_not_use_inline_task_without_runtime_support():
    parser = _build_parser()

    args = parser.parse_args([
        "snipe",
        "taneda",
        "--monitor",
    ])

    assert _should_use_inline_task(args) is False


def test_date_ranges_alone_use_inline_task_mode():
    parser = _build_parser()

    args = parser.parse_args([
        "run",
        "taneda",
        "--date-ranges", "2026-05-07:2026-05-09",
    ])

    assert _should_use_inline_task(args) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("run", ["run", "taneda", "--monitor"]),
        ("snipe", ["snipe", "taneda", "--monitor"]),
    ],
)
async def test_monitor_mode_fails_fast_until_runtime_support(command, argv, monkeypatch, caplog):
    parser = _build_parser()
    args = parser.parse_args(argv)
    cmd = getattr(main_module, f"_cmd_{command}")

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("snipe_all should not be called when monitor mode is unsupported")

    monkeypatch.setattr(main_module, "snipe_all", fail_if_called)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as excinfo:
            await cmd(args)

    assert excinfo.value.code == 2
    assert "--monitor is not supported yet" in caplog.text


@pytest.mark.asyncio
async def test_invalid_date_ranges_fail_as_cli_error(monkeypatch, caplog):
    parser = _build_parser()
    args = parser.parse_args([
        "run",
        "taneda",
        "--date-ranges", "not-a-range",
    ])

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("snipe_all should not be called for invalid --date-ranges")

    monkeypatch.setattr(main_module, "snipe_all", fail_if_called)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as excinfo:
            await main_module._cmd_run(args)

    assert excinfo.value.code == 2
    assert "Invalid --date-ranges value" in caplog.text


def test_build_inline_task_supports_launch_mode_without_date_preferences():
    args = argparse.Namespace(
        slug="taneda",
        size="1",
        target=None,
        date=[],
        exact_time=[],
        dates=None,
        exact_times=None,
        release_at="11:00",
        newly_released_only=True,
    )

    task = _build_inline_task(args)

    assert task.size == "1"
    assert task.launch is not None
    assert task.launch.newly_released_only is True
    assert [selector.to_dict() for selector in task.selectors] == [
        {"dates": []}
    ]


def test_run_uses_inline_launch_task_when_no_dates_are_provided():
    parser = _build_parser()

    args = parser.parse_args([
        "run",
        "taneda",
        "--size", "1",
        "--release-at", "11:00",
        "--newly-released-only",
    ])

    assert _should_use_inline_task(args) is True


def test_snipe_uses_inline_launch_task_when_no_dates_are_provided():
    parser = _build_parser()

    args = parser.parse_args([
        "snipe",
        "taneda",
        "--release-at", "11:00",
        "--newly-released-only",
    ])

    assert _should_use_inline_task(args) is True
