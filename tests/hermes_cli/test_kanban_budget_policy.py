"""Small-slice tests for issue #111305: versioned Kanban worker turn/phase budget policy."""

from hermes_cli.kanban_budget_policy import (
    ARCHETYPES,
    POLICY_VERSION,
    allocate,
    classify_task,
    describe,
    migrate_policy,
    phase_exhausted,
    suggest_total,
)


def test_each_archetype_classifies():
    cases = {
        "research this topic": "research",
        "implement the fix": "implementation",
        "verify the results with tests": "verification",
        "orchestrate the fan-out workflow": "orchestration",
    }
    for title, want in cases.items():
        got, _reason = classify_task(title=title, body="")
        assert got == want, f"{title!r} -> {got!r}, want {want!r}"


def test_unknown_fallback_is_explicit():
    got, reason = classify_task(title="asdf qwer zxcv", body="")
    assert got == "unknown"
    assert reason
    alloc = allocate(got)
    assert alloc["fallback"] is True
    assert alloc["archetype"] == "unknown"
    assert alloc["policy_version"] == POLICY_VERSION


def test_operator_override_bounded():
    alloc = allocate("research", total_override=100)
    assert alloc["total_turns"] == 100
    assert alloc["overridden"] is True
    assert sum(alloc["phases"].values()) == 100
    for bad in (0, 1, 1000):
        try:
            allocate("research", total_override=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"override {bad} should raise ValueError")


def test_phase_exhaustion_and_handoff_reserved():
    alloc = allocate("implementation")
    assert alloc["reserved_verify"] > 0
    assert alloc["reserved_handoff"] > 0
    assert phase_exhausted("work", alloc["phases"]["work"], alloc) is True
    assert phase_exhausted("work", alloc["phases"]["work"] - 1, alloc) is False
    assert phase_exhausted("handoff", alloc["phases"]["handoff"], alloc) is True


def test_policy_version_migration_preserves_override():
    old = allocate("research", total_override=90)
    old["policy_version"] = 0
    new = migrate_policy(old)
    assert new["policy_version"] == POLICY_VERSION
    assert new["total_turns"] == 90
    assert new["overridden"] is True


def test_history_suggest_never_rewrites_evidence():
    history = [
        {"archetype": "research", "used_turns": 50, "outcome": "completed"},
        {"archetype": "research", "used_turns": 60, "outcome": "completed"},
    ]
    snapshot = [dict(h) for h in history]
    thin = suggest_total("research", history)
    assert history == snapshot
    assert thin["fallback"] is True  # n<3: low coverage, conservative default
    rich = suggest_total(
        "research",
        history + [{"archetype": "research", "used_turns": 55, "outcome": "completed"}],
    )
    assert rich["fallback"] is False
    assert history == snapshot


def test_describe_shows_why_and_version():
    alloc = allocate("verification")
    text = describe(alloc)
    assert str(POLICY_VERSION) in text
    assert "verification" in text
    assert set(ARCHETYPES) >= {"research", "implementation", "verification", "orchestration", "unknown"}
