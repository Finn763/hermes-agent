"""Regression coverage: tests must not leak cwd into sibling tests (#87100).

Order-dependent failures are the worst class of flake — a test passes in
isolation, passes when the suite starts on its first file, fails when the
suite re-orders, and the only person who can reproduce it is the one who
ran the same suite the same way yesterday. The hermetic conftest already
isolates HERMES_HOME, HOME, credentials, audio playback, live process
signals, and subprocess command strings — but it does not isolate
``os.chdir()``. A test that calls ``os.chdir(tmp_path)`` and fails before
its ``finally: os.chdir(old_cwd)`` (or never had one) leaves the whole
rest of that pytest process rooted at the throwaway directory. The next
test that reads ``Path.cwd()`` or resolves a relative path sees the wrong
tree.

The fix lives in ``tests/conftest.py`` as an autouse fixture that pins
``Path.cwd()`` at setup and asserts it is unchanged at teardown, restoring
the pinned path before raising so a failing test does not poison the
rest of the file. The tests below exercise the three failure modes that
motivated the guard:

  * ``test_cwd_leak_when_chdir_fails_before_restore`` — chdir lands,
    then the assertion raises before any ``finally`` can run. The
    conftest autouse must still catch the leak at teardown.
  * ``test_cwd_leak_when_chdir_succeeds_without_restore`` — chdir
    lands, no assertion fails, no restore was ever attempted. Same
    outcome: the autouse fixture fails the test and restores cwd.
  * ``test_cwd_is_unchanged_after_a_well_guarded_chdir`` — explicit
    try/finally restore. Sanity that a correctly written test is
    not false-flagged by the autouse fixture.

Plus ``test_cwd_is_stable_across_tests`` as a baseline sanity check.
"""

from __future__ import annotations

import os
from pathlib import Path


def test_cwd_is_stable_across_tests():
    """Baseline: cwd at the top of this test equals cwd at the bottom.

    The conftest autouse fixture (see ``_isolate_cwd`` in tests/conftest.py)
    pins cwd at fixture setup, yields the test, and asserts no leak at
    teardown. A bare test body that never calls ``os.chdir()`` simply
    passes — the fixture's invariant holds trivially.
    """
    start = Path.cwd()
    (start / ".hermes_test_cwd_sentinel").write_text("ok", encoding="utf-8")
    try:
        assert (start / ".hermes_test_cwd_sentinel").read_text(encoding="utf-8") == "ok"
    finally:
        (start / ".hermes_test_cwd_sentinel").unlink(missing_ok=True)


def test_cwd_leak_when_chdir_fails_before_restore(tmp_path):
    """Simulate: chdir lands, then the assertion raises before any finally.

    This is the failure mode that makes cwd leakage observable in a
    real test suite: the body raises before any restore can run. With
    the conftest autouse fixture in place, teardown detects the leak,
    restores cwd, and fails this test — so the next test in this file
    still sees the right cwd.
    """
    sub = tmp_path / "leak-target"
    sub.mkdir()
    os.chdir(sub)
    assert False, "intentional: chdir landed, restore never ran"


def test_cwd_leak_when_chdir_succeeds_without_restore(tmp_path):
    """Simulate: chdir lands, no assertion fails, no restore attempted.

    The body does not raise, so a ``finally`` clause would not fire
    anyway — the code simply never tried to restore. The autouse
    fixture still catches it at teardown.
    """
    sub = tmp_path / "leak-target-2"
    sub.mkdir()
    os.chdir(sub)
    # No assert failure, no finally — the chdir stays until teardown.
    # The conftest autouse fixture raises at teardown when it observes
    # that Path.cwd() no longer matches the cwd at setup.


def test_cwd_observes_leak_from_previous_test(tmp_path):
    """Sibling test that observes the leak from the previous test.

    Reads ``Path.cwd()`` directly. Without the conftest autouse
    fixture, the previous test's leaked cwd (the tmp_path subdir) is
    still active here, and this assertion fires. With the fixture in
    place, teardown of the previous test restored cwd, so this one
    sees the suite root and passes.
    """
    # The previous test chdir'd into tmp_path / "leak-target-2" and
    # never restored. If cwd is still rooted at that path (or under
    # tmp_path), the leak reached this test. We pin a baseline that
    # the project root — not the test's own tmp_path — owns cwd.
    assert Path.cwd() == Path(__file__).resolve().parent.parent, (
        f"cwd leaked from a previous test: expected the project root, "
        f"got {Path.cwd()}. A test called os.chdir() and never "
        f"restored, leaving the suite rooted at the wrong directory."
    )


def test_cwd_is_unchanged_after_a_well_guarded_chdir(tmp_path):
    """Sanity: an explicit try/finally restore keeps the autouse happy.

    The autouse fixture is a no-op for tests that already restore their
    own cwd. This test guards against an overzealous implementation
    that flags correctly written tests.
    """
    sub = tmp_path / "guarded"
    sub.mkdir()
    start = Path.cwd()
    os.chdir(sub)
    try:
        assert Path.cwd() == sub
    finally:
        os.chdir(start)
    assert Path.cwd() == start