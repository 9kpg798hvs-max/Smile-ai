"""Exhaustive property test: no combination of findings that contains a
red trigger can ever map to green — regardless of what else is in the reply.

This brute-forces every combination of symptom attributes and boolean flags
(thousands of cases) rather than sampling. If a future rules change opens
any red→green path, this fails.
"""

import itertools

from triage.schema import (
    Color,
    ControlStatus,
    Findings,
    Severity,
    Symptom,
    SymptomCategory,
    Trajectory,
)
from triage.rules import ALWAYS_RED_CATEGORIES, triage


def all_symptoms():
    for cat, sev, traj, ctrl in itertools.product(
        SymptomCategory, Severity, Trajectory, ControlStatus
    ):
        yield Symptom(quote="q", category=cat, severity=sev, trajectory=traj, control=ctrl)


def is_red_trigger(s: Symptom) -> bool:
    return (
        s.category in ALWAYS_RED_CATEGORIES
        or s.severity is Severity.SEVERE
        or s.trajectory is Trajectory.WORSENING
        or s.control is ControlStatus.UNCONTROLLED
    )


def flag_combinations():
    keys = [
        "is_vague_or_minimal",
        "states_doing_well",
        "is_proxy_report",
        "immunocompromise_mentioned",
        "requests_contact",
        "unprompted_praise",
        "is_unintelligible_or_unrelated",
    ]
    for values in itertools.product([False, True], repeat=len(keys)):
        yield dict(zip(keys, values))


def test_red_trigger_symptom_never_green_under_any_flags():
    checked = 0
    for s in all_symptoms():
        if not is_red_trigger(s):
            continue
        for flags in flag_combinations():
            f = Findings(symptoms=[s], **flags)
            result = triage(f)
            assert result.color is Color.RED, (s, flags, result.color)
            checked += 1
    assert checked > 1000


def test_any_symptom_at_all_never_green():
    # Weaker but broader property: a reply that reports ANY symptom is never
    # green, whatever the flags say (SPEC §2.6 — a symptom is never a green).
    for s in all_symptoms():
        for flags in flag_combinations():
            f = Findings(symptoms=[s], **flags)
            assert triage(f).color is not Color.GREEN, (s, flags)


def test_no_affirmative_wellness_never_green():
    # Green is unreachable unless the patient affirmatively reports doing well.
    for flags in flag_combinations():
        if flags["states_doing_well"]:
            continue
        f = Findings(**flags)
        assert triage(f).color is not Color.GREEN, flags
