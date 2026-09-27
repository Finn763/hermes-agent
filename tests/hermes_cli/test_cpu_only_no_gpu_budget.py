"""CPU-only hosts must not budget RAM as GPU memory (#105389).

Windows Server 2022 with no GPU reported ~31GB 'GPU memory' and the catalog
recommended dense Qwen 27B, which then stalled 15 minutes on CPU prompt
processing. Root cause: probe_budget's no-NVIDIA branch returned a UMA budget
(RAM as VRAM at unified bandwidth) on every platform, including hosts with no
GPU at all.
"""

from __future__ import annotations

import sys

import hermes_cli.local_runtime.catalog as catalog
import hermes_cli.local_runtime.hardware as hw

GIB = 1 << 30
# Issue shape: 31.1 GiB box, no NVIDIA device, engine sees no allocator pool.
RAM_TOTAL = int(31.1 * GIB)
RAM_AVAIL = int(24.0 * GIB)


def _cpu_only_box(monkeypatch, *, platform="win32"):
    monkeypatch.setattr(hw, "_pool_probe_cache", None)
    monkeypatch.setattr(hw, "_nvidia_vram", lambda: None)
    monkeypatch.setattr(hw, "_ram_bytes", lambda: (RAM_TOTAL, RAM_AVAIL))
    monkeypatch.setattr(hw, "_device_pool_view", lambda: None)
    monkeypatch.setattr(sys, "platform", platform)


def test_cpu_only_planning_budget_claims_no_vram(monkeypatch):
    _cpu_only_box(monkeypatch)
    b = hw.probe_budget(planning=True)
    assert b.uma is False
    assert b.usable_vram_bytes == 0
    assert b.total_device_bytes == 0
    assert b.ram_available_bytes == RAM_TOTAL


def test_cpu_only_live_budget_claims_no_vram(monkeypatch):
    _cpu_only_box(monkeypatch)
    b = hw.probe_budget(planning=False)
    assert b.uma is False
    assert b.usable_vram_bytes == 0
    assert b.total_device_bytes == 0
    assert b.ram_available_bytes == RAM_AVAIL


def test_cpu_only_dense_27b_is_spilled_not_resident(monkeypatch):
    """The reported harm: 27B read as 'fits my GPU' (zero-spill). On a
    CPU-only host it must price as spilled from host RAM."""
    _cpu_only_box(monkeypatch)
    b = hw.probe_budget(planning=True)
    entry = catalog.catalog_by_id()["qwen3.8-27b"]
    choice = catalog.select_variant(entry, b)
    assert choice is not None  # still fits in RAM, just not on a GPU
    assert choice.zero_spill is False


def test_cpu_only_spilled_speed_uses_host_bandwidth(monkeypatch):
    _cpu_only_box(monkeypatch)
    b = hw.probe_budget(planning=True)
    entry = catalog.catalog_by_id()["qwen3.8-27b"]
    choice = catalog.select_variant(entry, b)
    tok_s = catalog.predicted_decode_tok_s(entry, choice.variant, b, spilled=True)
    per_token = max(1.0, choice.variant.size_bytes * entry.decode_fraction)
    assert tok_s == catalog._HOST_BANDWIDTH_GB_S * 1e9 / per_token
    assert tok_s < catalog._UMA_BANDWIDTH_GB_S * 1e9 / per_token


def test_darwin_no_gpu_keeps_unified_budget(monkeypatch):
    """Apple Silicon has no NVIDIA device either, but its shared pool is real:
    the darwin path must stay UMA."""
    _cpu_only_box(monkeypatch, platform="darwin")
    b = hw.probe_budget(planning=True)
    assert b.uma is True
    assert b.total_device_bytes == RAM_TOTAL
    assert b.usable_vram_bytes > 0
