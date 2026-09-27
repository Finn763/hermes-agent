"""Versioned Kanban worker turn/phase budget calibration policy (#111305).

Small slice: pure, inspectable recommendation policy. It classifies a task
into one documented archetype, derives a total turn budget plus a reserved
verify/handoff split, and keeps operator overrides explicit and bounded.
Historical outcomes only *suggest* totals (never mutate evidence); the
dispatcher enforces whatever it allocated, so history cannot silently
rewrite prior runs.
"""

from __future__ import annotations

POLICY_VERSION = 1

ARCHETYPES = ("research", "implementation", "verification", "orchestration", "unknown")

MIN_TOTAL_TURNS = 10
MAX_TOTAL_TURNS = 200

DEFAULT_TOTALS = {
    "research": 60,
    "implementation": 80,
    "verification": 30,
    "orchestration": 50,
    "unknown": 30,  # fail-closed conservative default
}

# (work, verify, handoff) fractions; verify+handoff always reserved.
_PHASE_SPLIT = {
    "research": (0.70, 0.20, 0.10),
    "implementation": (0.70, 0.20, 0.10),
    "verification": (0.60, 0.25, 0.15),
    "orchestration": (0.60, 0.20, 0.20),
    "unknown": (0.60, 0.25, 0.15),
}

_KEYWORDS = (
    ("research", ("research", "investigat", "survey", "explor", "literature", "analy")),
    ("implementation", ("implement", "build", "fix", "add ", "feature", "refactor", "code")),
    ("verification", ("verif", "test", "review", "audit", "validat", "check")),
    ("orchestration", ("orchestrat", "coordinat", "dispatch", "fan-out", "fanout", "delegat", "workflow", "multi-")),
)

MIN_HISTORY_SAMPLES = 3

__all__ = [
    "ARCHETYPES",
    "DEFAULT_TOTALS",
    "MAX_TOTAL_TURNS",
    "MIN_TOTAL_TURNS",
    "POLICY_VERSION",
    "allocate",
    "classify_task",
    "describe",
    "migrate_policy",
    "phase_exhausted",
    "suggest_total",
]


def classify_task(title="", body="", hint=None):
    """Return ``(archetype, reason)``; never raises, falls back to ``unknown``."""
    if hint is not None:
        h = str(hint).strip().lower()
        if h in ARCHETYPES:
            return h, "operator hint" if h != "unknown" else "operator hint: unknown"
        return "unknown", f"unrecognized hint {hint!r}; fallback"
    text = f"{title or ''}\n{body or ''}".lower()
    if not text.strip():
        return "unknown", "empty contract; fallback"
    for archetype, words in _KEYWORDS:
        if any(w in text for w in words):
            return archetype, f"keyword match: {archetype}"
    return "unknown", "no archetype keywords; fallback"


def _split_total(archetype, total):
    wf, vf, hf = _PHASE_SPLIT[archetype]
    verify = max(2, round(total * vf))
    handoff = max(1, round(total * hf))
    work = total - verify - handoff
    if work < 1:  # tiny totals: protect work last, keep reserves minimal
        verify, handoff = max(1, total - 2), 1
        work = total - verify - handoff
    return {"work": work, "verify": verify, "handoff": handoff}


def allocate(archetype="unknown", total_override=None):
    """Derive ``{total_turns, phases, ...}``; override must stay within bounds."""
    arch = str(archetype or "unknown").lower()
    reason = f"keyword match: {arch}" if arch in ARCHETYPES else None
    if arch not in ARCHETYPES:
        reason = f"unrecognized archetype {archetype!r}; fallback"
        arch = "unknown"
    fallback = arch == "unknown"
    if reason is None:
        reason = "no archetype keywords; fallback" if fallback else f"archetype default: {arch}"
    if total_override is not None:
        total = int(total_override)
        if not MIN_TOTAL_TURNS <= total <= MAX_TOTAL_TURNS:
            raise ValueError(f"total_override {total} outside [{MIN_TOTAL_TURNS},{MAX_TOTAL_TURNS}]")
        overridden = True
    else:
        total = DEFAULT_TOTALS[arch]
        overridden = False
    phases = _split_total(arch, total)
    return {
        "policy_version": POLICY_VERSION,
        "archetype": arch,
        "total_turns": total,
        "phases": phases,
        "reserved_verify": phases["verify"],
        "reserved_handoff": phases["handoff"],
        "fallback": fallback,
        "overridden": overridden,
        "reason": ("operator override; " if overridden else "") + reason,
    }


def phase_exhausted(phase, used_turns, allocation):
    """True when ``used_turns`` reached the phase allocation."""
    try:
        budget = allocation["phases"][phase]
    except (KeyError, TypeError):
        raise ValueError(f"unknown phase {phase!r}")
    return int(used_turns) >= int(budget)


def migrate_policy(old):
    """Upgrade a prior-version allocation dict to the current policy version."""
    arch = old.get("archetype", "unknown") if isinstance(old, dict) else "unknown"
    prev = old.get("policy_version", 0) if isinstance(old, dict) else 0
    if isinstance(old, dict) and old.get("overridden"):
        new = allocate(arch, total_override=old.get("total_turns"))
    else:
        new = allocate(arch)
    new["reason"] = f"migrated from policy v{prev}; {new['reason']}"
    return new


def suggest_total(archetype, history):
    """Recommendation-only hint from past runs; never mutates ``history``.

    # ponytail: median-of-used heuristic; replace with per-profile regression if it misleads.
    """
    arch = archetype if archetype in ARCHETYPES else "unknown"
    samples = sorted(
        int(h["used_turns"]) for h in (history or [])
        if isinstance(h, dict) and h.get("archetype") == arch
        and isinstance(h.get("used_turns"), int) and h["used_turns"] > 0
    )
    n = len(samples)
    if n < MIN_HISTORY_SAMPLES:
        return {
            "policy_version": POLICY_VERSION,
            "archetype": arch,
            "suggested_total": DEFAULT_TOTALS[arch],
            "coverage": "low",
            "fallback": True,
            "sample_count": n,
        }
    median = samples[n // 2]
    return {
        "policy_version": POLICY_VERSION,
        "archetype": arch,
        "suggested_total": max(MIN_TOTAL_TURNS, min(MAX_TOTAL_TURNS, median)),
        "coverage": "ok",
        "fallback": False,
        "sample_count": n,
    }


def describe(allocation):
    """One-line human explanation of why a budget was selected."""
    p = allocation["phases"]
    flag = " (operator override)" if allocation.get("overridden") else ""
    return (
        f"policy v{allocation['policy_version']}: archetype {allocation['archetype']}{flag} "
        f"-> total {allocation['total_turns']} turns "
        f"(work {p['work']} + verify {p['verify']} + handoff {p['handoff']}); "
        f"{allocation.get('reason', '')}"
    )
