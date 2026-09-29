"""Frequent-compaction breaker (#117574): N successful compactions in a window must trip the guard."""
from unittest.mock import patch
from agent.context_compressor import ContextCompressor


def _compressor(threshold_tokens=10000):
    cc = ContextCompressor(
        model="test-model", threshold_percent=0.75, protect_first_n=3,
        protect_last_n=20, quiet_mode=True, config_context_length=40960,
        provider="test",
    )
    cc.threshold_tokens = threshold_tokens
    return cc


class TestFrequentCompactionBreaker:
    def test_many_successful_compactions_in_window_block(self):
        cc = _compressor()
        base = 1000.0
        # 5 completions ~70s apart: every one "clears" the bar, so the
        # magnitude counter stays 0, but the frequency guard must trip.
        for i in range(5):
            with patch("agent.context_compressor.time.monotonic", return_value=base + i * 70.0):
                cc.record_completed_compaction()
        with patch("agent.context_compressor.time.monotonic", return_value=base + 5 * 70.0):
            assert cc._ineffective_compression_count == 0  # magnitude sees nothing
            assert cc.should_compress(cc.threshold_tokens + 1) is False

    def test_spaced_compactions_do_not_block(self):
        cc = _compressor()
        base = 2000.0
        for i in range(5):
            with patch("agent.context_compressor.time.monotonic", return_value=base + i * 3600.0):
                cc.record_completed_compaction()
        with patch("agent.context_compressor.time.monotonic", return_value=base + 5 * 3600.0):
            assert cc.should_compress(cc.threshold_tokens + 1) is True
