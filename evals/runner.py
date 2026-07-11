"""Eval harness: run the classifier over a labeled dataset and report.

The one metric that gates everything (SPEC §8): zero red-labeled cases
predicted green. The process exits non-zero if any exist, so CI fails.

Extractions are cached on (model, prompt hash, text, context) under
.eval_cache/ — re-running after a rules change is free; only prompt or
model changes re-hit the API.

Usage:
    python -m evals.runner evals/datasets/synthetic_seed.jsonl
    python -m evals.runner data/real_corpus.jsonl --model claude-opus-4-8
"""

import argparse
import datetime
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from triage.classifier import classify
from triage.schema import Color, Findings, VisitContext

CACHE_DIR = Path(".eval_cache")
RESULTS_DIR = Path("evals/results")


class EvalCase(BaseModel):
    id: str
    text: str
    label: Color
    context: VisitContext | None = None
    expect_proxy: bool | None = None
    notes: str | None = None


class CaseResult(BaseModel):
    id: str
    label: Color
    predicted: Color
    correct: bool
    is_red_as_green: bool  # the unacceptable failure
    is_soft_miss: bool  # red predicted yellow: survivable, tracked
    proxy_expected: bool | None = None
    proxy_detected: bool | None = None
    needs_manual_review: bool = False
    rationale: list[str] = Field(default_factory=list)
    text: str
    notes: str | None = None


class EvalReport(BaseModel):
    dataset: str
    model: str | None
    prompt_version: str | None
    run_at: str
    n_cases: int
    confusion: dict[str, dict[str, int]]  # confusion[label][predicted]
    red_recall_strict: float | None  # red predicted red
    red_recall_safe: float | None  # red predicted red-or-yellow (never green)
    red_as_green: list[str]  # case ids — MUST be empty
    soft_misses: list[str]  # red predicted yellow
    green_precision: float | None  # of predicted greens, how many were labeled green
    yellow_false_positive_rate: float | None  # labeled green, predicted yellow
    accuracy: float
    proxy_detection_accuracy: float | None
    gate_passed: bool
    cases: list[CaseResult]


def load_dataset(path: str | Path) -> list[EvalCase]:
    cases = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                cases.append(EvalCase.model_validate_json(line))
            except Exception as e:
                raise ValueError(f"{path}:{lineno}: bad case: {e}") from e
    ids = [c.id for c in cases]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    if dupes:
        raise ValueError(f"duplicate case ids: {dupes}")
    return cases


def _cache_key(extractor, case: EvalCase) -> str:
    payload = json.dumps(
        {
            "model": getattr(extractor, "model", "unknown"),
            "prompt": getattr(extractor, "prompt_hash", "unknown"),
            "text": case.text,
            "context": case.context.model_dump() if case.context else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class _CachingExtractor:
    """Wraps an extractor with a findings cache so rules changes re-run free."""

    def __init__(self, inner, cache_dir: Path):
        self.inner = inner
        self.cache_dir = cache_dir
        self.model = getattr(inner, "model", None)
        self.prompt_version = getattr(inner, "prompt_version", None)
        self.prompt_hash = getattr(inner, "prompt_hash", None)
        self._current_case: EvalCase | None = None

    def extract(self, reply_text, context=None) -> Findings:
        key = _cache_key(self.inner, self._current_case)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            return Findings.model_validate_json(path.read_text())
        findings = self.inner.extract(reply_text, context)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(findings.model_dump_json())
        return findings


def evaluate(
    cases: list[EvalCase],
    extractor,
    dataset_name: str = "dataset",
    cache_dir: Path | None = None,
    progress=None,
) -> EvalReport:
    wrapped = _CachingExtractor(extractor, cache_dir) if cache_dir else extractor

    results: list[CaseResult] = []
    for i, case in enumerate(cases):
        if cache_dir:
            wrapped._current_case = case
        outcome = classify(case.text, case.context, extractor=wrapped)
        results.append(
            CaseResult(
                id=case.id,
                label=case.label,
                predicted=outcome.color,
                correct=outcome.color is case.label,
                is_red_as_green=(case.label is Color.RED and outcome.color is Color.GREEN),
                is_soft_miss=(case.label is Color.RED and outcome.color is Color.YELLOW),
                proxy_expected=case.expect_proxy,
                proxy_detected=outcome.is_proxy_report if case.expect_proxy is not None else None,
                needs_manual_review=outcome.needs_manual_review,
                rationale=outcome.rationale,
                text=case.text,
                notes=case.notes,
            )
        )
        if progress:
            progress(i + 1, len(cases), results[-1])

    confusion: dict[str, dict[str, int]] = {
        l.value: {p.value: 0 for p in Color} for l in Color
    }
    for r in results:
        confusion[r.label.value][r.predicted.value] += 1

    reds = [r for r in results if r.label is Color.RED]
    greens = [r for r in results if r.label is Color.GREEN]
    predicted_green = [r for r in results if r.predicted is Color.GREEN]
    red_as_green = [r.id for r in results if r.is_red_as_green]
    soft_misses = [r.id for r in results if r.is_soft_miss]
    proxy_checked = [r for r in results if r.proxy_expected is not None]

    report = EvalReport(
        dataset=dataset_name,
        model=getattr(extractor, "model", None),
        prompt_version=getattr(extractor, "prompt_version", None),
        run_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        n_cases=len(results),
        confusion=confusion,
        red_recall_strict=(
            sum(r.predicted is Color.RED for r in reds) / len(reds) if reds else None
        ),
        red_recall_safe=(
            sum(r.predicted is not Color.GREEN for r in reds) / len(reds) if reds else None
        ),
        red_as_green=red_as_green,
        soft_misses=soft_misses,
        green_precision=(
            sum(r.label is Color.GREEN for r in predicted_green) / len(predicted_green)
            if predicted_green
            else None
        ),
        yellow_false_positive_rate=(
            sum(r.predicted is Color.YELLOW for r in greens) / len(greens) if greens else None
        ),
        accuracy=sum(r.correct for r in results) / len(results) if results else 0.0,
        proxy_detection_accuracy=(
            sum(r.proxy_detected == r.proxy_expected for r in proxy_checked) / len(proxy_checked)
            if proxy_checked
            else None
        ),
        gate_passed=not red_as_green,
        cases=results,
    )
    return report


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def render_report(report: EvalReport) -> str:
    lines = []
    lines.append(f"Eval: {report.dataset}  ({report.n_cases} cases)")
    lines.append(f"Model: {report.model}   prompt v{report.prompt_version}   {report.run_at}")
    lines.append("")
    lines.append("Confusion (rows = label, cols = predicted):")
    header = f"{'':>8}" + "".join(f"{p.value:>8}" for p in Color)
    lines.append(header)
    for l in Color:
        row = f"{l.value:>8}" + "".join(f"{report.confusion[l.value][p.value]:>8}" for p in Color)
        lines.append(row)
    lines.append("")
    lines.append(f"Red recall (strict, red→red):        {_pct(report.red_recall_strict)}")
    lines.append(f"Red recall (safe, red→red|yellow):   {_pct(report.red_recall_safe)}  <- must be 100%")
    lines.append(f"Soft misses (red→yellow):            {len(report.soft_misses)}  {report.soft_misses or ''}")
    lines.append(f"Green precision:                     {_pct(report.green_precision)}")
    lines.append(f"Yellow FP rate (green→yellow):       {_pct(report.yellow_false_positive_rate)}  (acceptable cost, SPEC §2.6)")
    lines.append(f"Proxy detection accuracy:            {_pct(report.proxy_detection_accuracy)}")
    lines.append(f"Overall accuracy:                    {_pct(report.accuracy)}")
    lines.append("")
    if report.gate_passed:
        lines.append("GATE PASSED: zero reds filed as green.")
    else:
        lines.append(f"GATE FAILED: {len(report.red_as_green)} red case(s) filed as GREEN:")
        for r in report.cases:
            if r.is_red_as_green:
                lines.append(f"  [{r.id}] {r.text!r}")
                for reason in r.rationale:
                    lines.append(f"      rationale: {reason}")
    wrong = [r for r in report.cases if not r.correct and not r.is_red_as_green]
    if wrong:
        lines.append("")
        lines.append("Other misclassifications:")
        for r in wrong:
            lines.append(f"  [{r.id}] label={r.label.value} predicted={r.predicted.value}: {r.text!r}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="JSONL dataset (see evals/DATA_FORMAT.md)")
    parser.add_argument("--model", default=None, help="Override TRIAGE_MODEL")
    parser.add_argument("--no-cache", action="store_true", help="Bypass the extraction cache")
    args = parser.parse_args(argv)

    from triage.extractor import DEFAULT_MODEL, Extractor

    extractor = Extractor(model=args.model or DEFAULT_MODEL)
    cases = load_dataset(args.dataset)

    def progress(done, total, result):
        mark = "!" if result.is_red_as_green else ("~" if not result.correct else ".")
        print(f"\r{done}/{total} {mark}", end="", file=sys.stderr, flush=True)

    report = evaluate(
        cases,
        extractor,
        dataset_name=str(args.dataset),
        cache_dir=None if args.no_cache else CACHE_DIR,
        progress=progress,
    )
    print(file=sys.stderr)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report.run_at.replace(":", "-").split(".")[0]
    out = RESULTS_DIR / f"{stamp}.json"
    out.write_text(report.model_dump_json(indent=2))
    (RESULTS_DIR / "latest.json").write_text(report.model_dump_json(indent=2))

    print(render_report(report))
    print(f"\nFull results: {out}")
    return 0 if report.gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
