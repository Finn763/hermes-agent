"""Determinate phase progress for ``hermes update`` (#122691).

The update pipeline printed one bare arrow line per phase, so users could not
tell how much download/install work was left before the app restarted
(#122691). ``hermes_cli.update_cmd`` opens a fixed phase plan for the run's
path with ``begin()`` and advances it with ``step()`` at each phase boundary;
each step renders ``[k/N]`` plus a bar, the ZIP transports' byte counts extend
the current phase's slot through ``byte_progress()``, and
``replan()``/``finish()``/``fail()`` keep the sequence honest when the path
changes (git->ZIP fallback), a planned tail is skipped (already up to date) or
the run dies. Every render is forwarded to a listener so tests can assert the
emitted sequence without scraping stdout.

ASCII-only by house rule: labels and rendered lines are plain text.
"""

from __future__ import annotations

from typing import Callable, Optional

BAR_WIDTH = 20


class _Progress:
    """One update run's fixed phase plan and the position it has reached."""

    def __init__(self, phases: tuple[str, ...]) -> None:
        self.phases = phases
        self.index = 0
        self.last_percent = -1


_ACTIVE: Optional[_Progress] = None
_LISTENER: Optional[Callable[[dict], None]] = None


def set_listener(listener: Optional[Callable[[dict], None]]) -> None:
    """Forward every emitted event to *listener*; None disables forwarding (tests)."""
    global _LISTENER
    _LISTENER = listener


def begin(phases) -> None:
    """Open the progress plan for this run; replaces any plan still active."""
    global _ACTIVE
    _ACTIVE = _Progress(tuple(phases))


def end() -> None:
    """Close the active plan (run finished, or test cleanup)."""
    global _ACTIVE
    _ACTIVE = None


def replan(phases) -> None:
    """Replace the plan's not-yet-run tail. The git->ZIP fallback steps phases the
    original plan never listed; folding them in keeps ``[k/N]`` in-plan and lets the
    run end at ``[N/N]`` instead of ``[...]`` (#122691)."""
    progress = _ACTIVE
    if progress is None:
        return
    progress.phases = tuple(progress.phases[:progress.index]) + tuple(phases)


def finish(label: str) -> None:
    """Render *label* as the plan's final phase at 100%. Paths that skip planned
    phases (already up to date skips ``Pull update``) must not stop at 75% (#122691)."""
    progress = _ACTIVE
    if progress is None:
        return
    progress.index = len(progress.phases)
    _emit({"kind": "phase", "index": progress.index, "total": len(progress.phases),
           "label": label})
    print(f"  {_bracket(progress)} {_bar(1.0)} {label}")


def fail() -> None:
    """Mark the active plan failed and close it. A run that exits mid-sequence must
    not leave its last ``[k/N]`` line reading as in-flight work (#122691)."""
    global _ACTIVE
    progress = _ACTIVE
    if progress is None:
        return
    phase = (progress.phases[progress.index - 1]
             if 0 < progress.index <= len(progress.phases) else "the update")
    _emit({"kind": "failed", "stage": phase, "index": progress.index,
           "total": len(progress.phases)})
    print(f"  [--/{len(progress.phases)}] Update failed at: {phase}")
    _ACTIVE = None


def _bar(fraction: float) -> str:
    filled = max(0, min(BAR_WIDTH, int(round(fraction * BAR_WIDTH))))
    return "[" + "#" * filled + "-" * (BAR_WIDTH - filled) + "]"


def _bracket(progress: _Progress) -> str:
    total = len(progress.phases)
    if total and progress.index <= total:
        return f"[{progress.index}/{total}]"
    # Plan exhausted (for example the ZIP fallback after a git failure steps
    # phases the original plan never listed): keep reporting the label
    # honestly instead of printing a bracket that under-counts.
    return "[...]"


def _emit(event: dict) -> None:
    if _LISTENER is not None:
        try:
            _LISTENER(event)
        except Exception:
            # A progress listener (tests, hooks) must never break an update.
            pass


def step(label: str) -> None:
    """Advance into *label* and print its ``[k/N]`` progress line."""
    progress = _ACTIVE
    if progress is None:
        return
    progress.index += 1
    _emit({"kind": "phase", "index": progress.index, "total": len(progress.phases),
           "label": label})
    print(f"  {_bracket(progress)} {_bar(progress.index / max(1, len(progress.phases)))} {label}")


def byte_progress(read: int, size: int) -> None:
    """Byte sub-progress for the current phase; silent when the size is unknown."""
    progress = _ACTIVE
    if progress is None or progress.index == 0 or size <= 0:
        return
    read = max(0, min(read, size))
    percent = min(100, int(read * 100 / size))
    _emit({"kind": "bytes", "index": progress.index, "total": len(progress.phases),
           "read": read, "size": size})
    # ponytail: one line per changed percent (<= 100 lines per transport); a live
    # \r redraw is the upgrade path if byte-level granularity is ever needed.
    if percent == progress.last_percent:
        return
    progress.last_percent = percent
    # Continue the current phase's slot on the ``step`` scale: phase ``k`` entered at
    # ``[k/N]`` and finishes at ``[(k+1)/N]``, so the bar never resets to empty when
    # the bytes start (#122691).
    slot = (progress.index + read / size) / max(1, len(progress.phases))
    print(f"  {_bracket(progress)} {_bar(slot)} {percent}% ({read}/{size} bytes)")
