"""rebind() must memoize contextmanager-wrapped helpers (issue #105238)."""
import contextlib

from tui_gateway.method_ctx import rebind


def _closed_cm(fn):
    for c in (fn.__closure__ or ()):
        try:
            v = c.cell_contents
        except ValueError:
            continue
        if getattr(v, "__wrapped__", None) is not None:
            return v
    return None


def test_cm_helper_shared_by_two_closures_rebinds_to_same_object():
    @contextlib.contextmanager
    def helper():
        yield 42

    def h1():
        return helper

    def h2():
        return helper

    g = dict(globals())
    seen: dict = {}
    rh1 = rebind(h1, g, seen)
    rh2 = rebind(h2, g, seen)
    rb1, rb2 = _closed_cm(rh1), _closed_cm(rh2)
    assert rb1 is not None and rb2 is not None
    assert rb1 is rb2
    assert seen[id(helper)] is rb1
    # rebound helper still works against new globals
    with rb1() as v:
        assert v == 42


def test_plain_function_memoization_unaffected():
    def plain():
        return 1

    seen: dict = {}
    assert rebind(plain, dict(globals()), seen) is rebind(plain, dict(globals()), seen)
