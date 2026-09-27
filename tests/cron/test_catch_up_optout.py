"""Per-job catch-up opt-out (#111212).

A job scheduled `0 16 * * 5` (Friday 16:00) whose host slept until Sunday
10:06 (~42h late) must NOT fire when the job opts out with
``catch_up=False``: the stale occurrence is skipped, ``next_run_at`` is
re-anchored forward, and the skip is stamped on ``last_dispatch`` so it is
visible. Jobs without the opt-out keep today's behaviour (fire once now).
"""

from datetime import datetime, timedelta, timezone

import pytest

from cron.jobs import (
    get_due_jobs,
    load_jobs,
    save_jobs,
)

# Sunday 2026-09-06 10:06 UTC; the previous Friday 16:00 was ~42h earlier.
FIXED_NOW = datetime(2026, 9, 6, 10, 6, 0, tzinfo=timezone.utc)
FRIDAY_16H = datetime(2026, 9, 4, 16, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def cron_store(tmp_path, monkeypatch):
    """Redirect cron storage to a temp dir and pin the clock."""
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: FIXED_NOW)
    return tmp_path


def _friday_job(jid, **extra):
    job = {
        "id": jid,
        "name": jid,
        "prompt": "x",
        "schedule": {"kind": "cron", "expr": "0 16 * * 5"},
        "next_run_at": FRIDAY_16H.isoformat(),
        "last_run_at": None,
        "enabled": True,
        "state": "scheduled",
        "repeat": {"times": None, "completed": 0},
        "deliver": "local",
    }
    job.update(extra)
    return job


def _hourly_job(jid, next_run_dt, **extra):
    job = {
        "id": jid,
        "name": jid,
        "prompt": "x",
        "schedule": {"kind": "interval", "minutes": 60},
        "next_run_at": next_run_dt.isoformat(),
        "last_run_at": None,
        "enabled": True,
        "state": "scheduled",
        "repeat": {"times": None, "completed": 0},
        "deliver": "local",
    }
    job.update(extra)
    return job


class TestCatchUpOptOut:
    def test_opted_out_stale_job_does_not_fire(self, cron_store):
        save_jobs([_friday_job("friday", catch_up=False)])

        due = get_due_jobs()

        assert [d["id"] for d in due] == []

    def test_opted_out_stale_job_reanchored_and_skip_stamped(self, cron_store):
        save_jobs([_friday_job("friday", catch_up=False)])

        get_due_jobs()
        persisted = load_jobs()[0]

        assert datetime.fromisoformat(persisted["next_run_at"]) > FIXED_NOW
        stamp = persisted["last_dispatch"]
        assert stamp["kind"] == "skipped_stale"
        assert stamp["scheduled_at"] == FRIDAY_16H.isoformat()
        assert stamp["lateness_seconds"] == pytest.approx(
            (FIXED_NOW - FRIDAY_16H).total_seconds(), abs=1)

    def test_default_job_still_catches_up(self, cron_store):
        save_jobs([_friday_job("friday")])

        due = get_due_jobs()

        assert [d["id"] for d in due] == ["friday"]
        assert due[0]["last_dispatch"]["kind"] == "catch_up"

    def test_opted_out_job_within_grace_still_fires(self, cron_store):
        # 10 min late on an hourly job is inside the 30 min grace window.
        scheduled = FIXED_NOW - timedelta(minutes=10)
        save_jobs([_hourly_job("hourly", scheduled, catch_up=False)])

        due = get_due_jobs()

        assert [d["id"] for d in due] == ["hourly"]

    def test_update_door_opts_out(self, cron_store):
        from cron.jobs import update_job

        save_jobs([_friday_job("friday")])
        assert [d["id"] for d in get_due_jobs()] == ["friday"]

        save_jobs([_friday_job("friday")])
        updated = update_job("friday", {"catch_up": False})
        assert updated["catch_up"] is False

        assert [d["id"] for d in get_due_jobs()] == []


class TestMisfireSweepHonoursOptOut:
    def test_sweep_skips_opted_out_stale_job(self, cron_store):
        import threading

        from cron.jobs import create_job
        from cron.scheduler_provider import CronScheduler, fire_overdue_jobs

        class RecordingProvider(CronScheduler):
            def __init__(self):
                self.fired = []
                self._done = threading.Event()

            @property
            def name(self):
                return "recording"

            def start(self, stop_event, **kw):  # pragma: no cover - unused
                return None

            def fire_claimed(self, claimed_job, *, adapters=None, loop=None,
                             cancel_event=None):
                self.fired.append(claimed_job["id"])
                self._done.set()
                return True

        opted_out = create_job(prompt="p", schedule="every 1h", catch_up=False)
        assert opted_out["catch_up"] is False
        default = create_job(prompt="q", schedule="every 1h")
        assert "catch_up" not in default
        jobs = load_jobs()
        for j in jobs:
            # 42h overdue: far past the hourly 30 min grace window.
            j["next_run_at"] = (FIXED_NOW - timedelta(hours=42)).isoformat()
        save_jobs(jobs)

        provider = RecordingProvider()
        assert fire_overdue_jobs(provider, now=FIXED_NOW) == 1
        assert provider._done.wait(5.0)
        # The default job still catches up; the opted-out stale job never fires.
        assert provider.fired == [default["id"]]
