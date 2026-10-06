"""#120328: a cron worker killed after adoption must not leave the job untouched.

A restart-safe worker that dies after adopting its execution (e.g. systemd
OOM-kills its scope) is recovered to ``unknown`` by the gateway waiter — but
nothing updates the job: ``last_run_at``/``last_status``/``last_error`` keep
the previous run's values, no ``cron_incidents`` row opens, nothing is
delivered, and the gateway logs no error.
"""
from __future__ import annotations

import logging
from unittest.mock import Mock


def test_pre_recovered_row_still_recorded_on_worker_exit(
    tmp_path, monkeypatch, caplog
):
    """#120328 follow-up: a competing reaper (tick dead-owner reap / manual pre-dispatch)
    can move the dead worker's row to ``unknown`` before this waiter's post-wait read.
    The exit then sees a terminal row and must still record the killed run — otherwise
    the job keeps saying the run never happened."""
    import cron.scheduler as scheduler
    import cron.executions as executions
    import cron.incidents as incidents
    from cron.jobs import (
        claim_job_for_fire, create_job, get_job, use_cron_store,
    )

    monkeypatch.setattr(
        executions, "EXECUTIONS_FILE", tmp_path / "executions.db")
    with use_cron_store(tmp_path):
        job = create_job(prompt="x", schedule="every 5m", name="killed-race")
        jid = job["id"]
        assert claim_job_for_fire(jid) is True
        pre_recovered = {
            "id": "exec-1", "job_id": jid, "status": "unknown",
            "error": executions._OWNER_GONE_REASON,
        }
        monkeypatch.setattr(
            scheduler, "get_execution", lambda _eid: dict(pre_recovered))
        recover = Mock(return_value=0)  # recovery already ran elsewhere
        monkeypatch.setattr(
            scheduler, "recover_interrupted_executions", recover)
        delivered = Mock(return_value=None)
        monkeypatch.setattr(scheduler, "_deliver_result", delivered)

        process = Mock()
        process.wait.return_value = 143

        with caplog.at_level(logging.ERROR):
            assert scheduler._wait_for_external_cron_worker(
                process, execution_id="exec-1", job_id=jid) is True

        after = get_job(jid)
        assert after["last_run_at"] is not None
        assert after["last_status"] == "error"
        assert "143" in (after["last_error"] or "")
        rows = [r for r in incidents.list_incidents()
                if r["job_id"] == jid]
        assert rows, "reaper-pre-recovered killed run must still open an incident"
        assert delivered.called, "reaper-pre-recovered killed run must deliver"


def test_pre_recovered_row_still_recorded_on_wait_timeout(
    tmp_path, monkeypatch, caplog
):
    """The worker-liveness exit (wait timeout) can also see a row a competing reaper
    already recovered to ``unknown`` (dead-owner / wedged release): the bookkeeping
    must fire there too, with no exit code available yet."""
    import subprocess
    import cron.scheduler as scheduler
    import cron.executions as executions
    import cron.incidents as incidents
    from cron.jobs import (
        claim_job_for_fire, create_job, get_job, use_cron_store,
    )

    monkeypatch.setattr(
        executions, "EXECUTIONS_FILE", tmp_path / "executions.db")
    with use_cron_store(tmp_path):
        job = create_job(prompt="x", schedule="every 5m", name="killed-wedged")
        jid = job["id"]
        assert claim_job_for_fire(jid) is True
        pre_recovered = {
            "id": "exec-1", "job_id": jid, "status": "unknown",
            "error": executions._OWNER_WEDGED_REASON,
        }
        monkeypatch.setattr(
            scheduler, "get_execution", lambda _eid: dict(pre_recovered))
        recover = Mock(return_value=0)
        monkeypatch.setattr(
            scheduler, "recover_interrupted_executions", recover)
        delivered = Mock(return_value=None)
        monkeypatch.setattr(scheduler, "_deliver_result", delivered)

        process = Mock()
        process.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="cron-worker", timeout=1.0), 137,
        ]

        with caplog.at_level(logging.ERROR):
            assert scheduler._wait_for_external_cron_worker(
                process, execution_id="exec-1", job_id=jid) is True

        after = get_job(jid)
        assert after["last_run_at"] is not None
        assert after["last_status"] == "error"
        rows = [r for r in incidents.list_incidents()
                if r["job_id"] == jid]
        assert rows, "wedged pre-recovered killed run must still open an incident"
        assert delivered.called, "wedged pre-recovered killed run must deliver"


def test_flapping_kill_codes_share_one_incident(tmp_path, monkeypatch):
    """A kill that flaps between signals (137 vs 143) must refresh ONE incident for the
    recovery reason — not mint a fresh row per code and reset the repeat-alert cooldown."""
    import cron.scheduler as scheduler
    import cron.executions as executions
    import cron.incidents as incidents
    from cron.jobs import create_job, use_cron_store

    monkeypatch.setattr(
        executions, "EXECUTIONS_FILE", tmp_path / "executions.db")
    with use_cron_store(tmp_path):
        job = create_job(prompt="x", schedule="every 5m", name="flap")
        jid = job["id"]
        for base in (executions._OWNER_GONE_REASON,
                     executions._OWNER_WEDGED_REASON):
            scheduler._upsert_incident_for_failure(
                job, f"{base} (cron worker exited with code 137)")
            scheduler._upsert_incident_for_failure(
                job, f"{base} (cron worker exited with code 143)")
        rows = [r for r in incidents.list_incidents()
                if r["job_id"] == jid]
        assert len(rows) == 2, (
            "one incident per recovery reason across kill codes, got "
            f"{len(rows)}: {[r['error'] for r in rows]}")


def test_killed_worker_after_adopt_updates_job_incident_and_log(
    tmp_path, monkeypatch, caplog
):
    import cron.scheduler as scheduler
    import cron.executions as executions
    import cron.incidents as incidents
    from cron.jobs import (
        claim_job_for_fire, create_job, get_job, use_cron_store,
    )

    monkeypatch.setattr(
        executions, "EXECUTIONS_FILE", tmp_path / "executions.db")
    with use_cron_store(tmp_path):
        job = create_job(prompt="x", schedule="every 5m", name="killed")
        jid = job["id"]
        assert claim_job_for_fire(jid) is True
        assert get_job(jid)["fire_claim"]["by"]

        recovery_error = executions._OWNER_GONE_REASON
        states = iter([
            {"id": "exec-1", "job_id": jid, "status": "running"},
            {"id": "exec-1", "job_id": jid, "status": "unknown",
             "error": recovery_error},
            {"id": "exec-1", "job_id": jid, "status": "unknown",
             "error": recovery_error},
        ])
        monkeypatch.setattr(
            scheduler, "get_execution", lambda _eid: next(states))
        recover = Mock(return_value=1)
        monkeypatch.setattr(
            scheduler, "recover_interrupted_executions", recover)
        delivered = Mock(return_value=None)
        monkeypatch.setattr(scheduler, "_deliver_result", delivered)

        process = Mock()
        process.wait.return_value = 137
        process.poll.return_value = 137

        with caplog.at_level(logging.ERROR):
            assert scheduler._wait_for_external_cron_worker(
                process, execution_id="exec-1", job_id=jid) is True

        after = get_job(jid)
        assert after["last_run_at"] is not None
        assert after["last_status"] == "error"
        assert "137" in (after["last_error"] or "")
        rows = [r for r in incidents.list_incidents()
                if r["job_id"] == jid]
        assert rows, "killed run must open a cron_incidents row"
        assert delivered.called, "killed run must attempt failure delivery"
        assert any(jid in r.getMessage() or "exec-1" in r.getMessage()
                   for r in caplog.records), \
            "killed run must log a gateway error"
