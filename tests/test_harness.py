"""End-to-end tests of the eval harness, offline via mock extractors."""

from pathlib import Path

from evals.runner import EvalCase, evaluate, load_dataset, render_report
from triage.classifier import classify
from triage.schema import Color

from .mock_extractor import (
    AlwaysGreenExtractor,
    CountingExtractor,
    ExplodingExtractor,
    KeywordExtractor,
)

SEED = Path(__file__).parent.parent / "evals" / "datasets" / "synthetic_seed.jsonl"


def test_seed_dataset_loads_and_is_well_formed():
    cases = load_dataset(SEED)
    assert len(cases) >= 25
    assert {c.label for c in cases} == {Color.RED, Color.YELLOW, Color.GREEN}
    # Every case documents why it's labeled the way it is.
    assert all(c.notes for c in cases)


def test_full_pipeline_on_seed_with_keyword_mock_passes_gate():
    """The keyword mock is a stand-in for correct extraction. If extraction is
    right, the rules must put every red-labeled seed case at red — proving the
    dataset labels and the rules layer agree, and exercising the whole
    harness path (classify -> metrics -> gate)."""
    cases = load_dataset(SEED)
    report = evaluate(cases, KeywordExtractor(), dataset_name="seed")
    assert report.n_cases == len(cases)
    # Confusion matrix accounts for every case exactly once.
    assert sum(sum(row.values()) for row in report.confusion.values()) == len(cases)
    assert report.gate_passed, f"red->green cases: {report.red_as_green}"
    assert report.red_recall_safe == 1.0
    # Report renders without error and includes the gate line.
    text = render_report(report)
    assert "GATE PASSED" in text


def test_gate_fails_when_reds_land_green():
    cases = load_dataset(SEED)
    report = evaluate(cases, AlwaysGreenExtractor(), dataset_name="seed")
    assert not report.gate_passed
    assert len(report.red_as_green) == sum(1 for c in cases if c.label is Color.RED)
    assert "GATE FAILED" in render_report(report)


def test_extraction_cache_prevents_repeat_calls(tmp_path):
    cases = load_dataset(SEED)[:5]
    counting = CountingExtractor(KeywordExtractor())
    evaluate(cases, counting, cache_dir=tmp_path)
    assert counting.calls == 5
    evaluate(cases, counting, cache_dir=tmp_path)
    assert counting.calls == 5  # second run served entirely from cache


def test_classifier_fails_safe_to_yellow_on_extractor_error():
    result = classify("my face is swollen", extractor=ExplodingExtractor())
    assert result.color is Color.YELLOW
    assert result.needs_manual_review
    assert result.rationale


def test_spec_archetype_case_via_pipeline():
    # SPEC §2.2's exact failure mode, through classify() with the mock.
    text = (
        "Hope all is well! Everything is going very well. I have noticed a "
        "little swelling in my cheek and the tooth is sensitive to cold. "
        "I finished chemo four weeks ago."
    )
    result = classify(text, extractor=KeywordExtractor())
    assert result.color is Color.RED


def test_case_result_carries_rationale_for_debugging():
    cases = [EvalCase(id="x", text="ok", label=Color.YELLOW)]
    report = evaluate(cases, KeywordExtractor())
    assert report.cases[0].rationale
