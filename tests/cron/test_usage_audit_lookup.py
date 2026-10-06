"""Review follow-up for #100212: the manual-run usage-audit lookup.

- The reader scans the audit back-to-front and stops at the first match (the
  file grows one line per fire), instead of re-parsing all history per run.
- Records are matched by ``job_id`` AND the per-fire claim owner, so a
  concurrent run of the same job cannot donate its counters — the flip
  direction is success -> failed.
- The scheduler's zero-inference guard treats a missing ``api_calls`` key as
  zero instead of silently no-oping (``None == 0`` is False).
"""
from __future__ import annotations

import json

from cron import scheduler


def _audit(tmp_path, monkeypatch, lines):
    path = tmp_path / "usage_audit.jsonl"
    path.write_bytes(b"\n".join(lines) + b"\n")
    monkeypatch.setattr(scheduler, "_usage_audit_path", lambda: path)
    return path


class TestLatestUsageAuditRecord:
    def test_matches_own_fire_not_a_newer_concurrent_record(self, tmp_path, monkeypatch):
        mine = {"job_id": "job-a", "fire_owner": "owner-mine", "api_calls": 0, "total_tokens": 0}
        other = {"job_id": "job-a", "fire_owner": "owner-other", "api_calls": 7, "total_tokens": 99}
        _audit(tmp_path, monkeypatch, [json.dumps(mine).encode(), json.dumps(other).encode()])

        record = scheduler._latest_usage_audit_record("job-a", "owner-mine")

        assert record is not None
        assert record["fire_owner"] == "owner-mine"
        assert record["api_calls"] == 0

    def test_owner_filter_is_strict_about_records_without_the_field(self, tmp_path, monkeypatch):
        legacy = {"job_id": "job-a", "api_calls": 1, "total_tokens": 4}
        _audit(tmp_path, monkeypatch, [json.dumps(legacy).encode()])

        assert scheduler._latest_usage_audit_record("job-a", "owner-mine") is None

    def test_without_owner_keeps_job_id_only_lookup(self, tmp_path, monkeypatch):
        older = {"job_id": "job-a", "api_calls": 1}
        newer = {"job_id": "job-a", "api_calls": 2}
        _audit(tmp_path, monkeypatch, [json.dumps(older).encode(), json.dumps(newer).encode()])

        record = scheduler._latest_usage_audit_record("job-a")

        assert record is not None and record["api_calls"] == 2

    def test_skips_other_jobs_and_corrupt_lines(self, tmp_path, monkeypatch):
        _audit(tmp_path, monkeypatch, [
            b"{not json",
            json.dumps({"job_id": "job-b", "api_calls": 9}).encode(),
            json.dumps({"job_id": "job-a", "api_calls": 3}).encode(),
            b"[]",  # a JSON array is not a record
            json.dumps({"job_id": "job-b", "api_calls": 8}).encode(),
        ])

        record = scheduler._latest_usage_audit_record("job-a")

        assert record is not None and record["api_calls"] == 3

    def test_finds_a_record_far_back_across_chunk_boundaries(self, tmp_path, monkeypatch):
        records = [{"job_id": f"job-{i}", "api_calls": i} for i in range(40)]
        records.insert(7, {"job_id": "job-target", "api_calls": 42})
        _audit(tmp_path, monkeypatch, [json.dumps(r).encode() for r in records])
        monkeypatch.setattr(scheduler, "_AUDIT_READ_CHUNK", 17)  # force split lines

        record = scheduler._latest_usage_audit_record("job-target")

        assert record is not None and record["api_calls"] == 42

    def test_missing_file_and_unknown_job_return_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler, "_usage_audit_path", lambda: tmp_path / "nope.jsonl")
        assert scheduler._latest_usage_audit_record("job-a") is None

        _audit(tmp_path, monkeypatch, [json.dumps({"job_id": "job-b"}).encode()])
        assert scheduler._latest_usage_audit_record("job-a", "owner-x") is None


class TestZeroInferenceResult:
    def test_missing_api_calls_fails_closed(self):
        assert scheduler._zero_inference_result({}) is True

    def test_tokens_without_api_calls_are_not_zero_inference(self):
        assert scheduler._zero_inference_result({"total_tokens": 5, "prompt_tokens": 3}) is False

    def test_zero_calls_zero_tokens(self):
        assert scheduler._zero_inference_result({"api_calls": 0, "total_tokens": 0}) is True

    def test_zero_calls_with_prompt_tokens_only(self):
        result = {"api_calls": 0, "total_tokens": 9, "prompt_tokens": 0}
        assert scheduler._zero_inference_result(result) is True

    def test_positive_calls_are_not_zero_inference(self):
        assert scheduler._zero_inference_result({"api_calls": 2}) is False
