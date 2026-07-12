"""Deterministic keyword-based extractor for offline tests.

This exists to exercise the harness plumbing and rules layer without an API
key. It is NOT the classifier and is intentionally not importable from the
triage package — never use it for real triage.
"""

from triage.schema import (
    ControlStatus,
    Findings,
    QuestionTopic,
    Severity,
    Symptom,
    SymptomCategory,
    Trajectory,
    VisitContext,
)


class KeywordExtractor:
    model = "mock-keyword"
    prompt_version = "mock"
    prompt_hash = "mock"

    def extract(self, text: str, context: VisitContext | None = None) -> Findings:
        low = text.lower()
        f = Findings()

        def add(category, quote, severity=Severity.UNSTATED, trajectory=Trajectory.UNSTATED,
                control=ControlStatus.UNSTATED):
            f.symptoms.append(
                Symptom(quote=quote, category=category, severity=severity,
                        trajectory=trajectory, control=control)
            )

        if "swell" in low or "swollen" in low or "puffed up" in low:
            add(SymptomCategory.SWELLING, "swelling")
        if "numb" in low and "number" not in low:
            add(SymptomCategory.ALTERED_SENSATION, "numbness")
        if "came out" in low or "came off" in low or "fell out" in low:
            add(SymptomCategory.TEMPORARY_RESTORATION_PROBLEM, "restoration came out/off")
        if "rash" in low or "reaction to" in low:
            add(SymptomCategory.MEDICATION_REACTION, "medication reaction")
        if "fever" in low:
            add(SymptomCategory.FEVER_OR_MALAISE, "fever")
        if "bleeding" in low:
            add(SymptomCategory.BLEEDING, "bleeding", severity=Severity.MILD,
                trajectory=Trajectory.STABLE)
        if "sensitive to cold" in low:
            add(SymptomCategory.TEMPERATURE_SENSITIVITY, "cold sensitivity")

        pain_words = ("sore", "ache", "throbbing", "pain", "tender")
        pain_negations = ("no pain", "zero pain", "pain free", "pain-free")
        if any(w in low for w in pain_words) and not any(n in low for n in pain_negations):
            severity = Severity.SEVERE if "worst pain" in low else Severity.UNSTATED
            trajectory = Trajectory.UNSTATED
            if "getting worse" in low or "worse tonight" in low:
                trajectory = Trajectory.WORSENING
            elif "better than yesterday" in low:
                trajectory = Trajectory.IMPROVING
            control = ControlStatus.UNSTATED
            if "aren't touching it" in low:
                control = ControlStatus.UNCONTROLLED
            elif "handling it" in low:
                control = ControlStatus.CONTROLLED
            add(SymptomCategory.PAIN, "pain", severity, trajectory, control)

        for signal in ("hard to swallow", "trouble breathing", "can't breathe",
                       "won't stop bleeding", "bleeding won't stop", "allergic",
                       "throat is swelling", "hives"):
            if signal in low:
                f.emergency_signals.append(signal)

        if "who is this" in low or "wrong number" in low:
            f.is_unintelligible_or_unrelated = True
        elif "?" in text:
            f.questions.append(text[text.rfind("?") - 40 : text.rfind("?") + 1].strip())
            if any(w in low for w in ("ibuprofen", "antibiotic", "medication", "pill", "dose")):
                f.question_topics.append(QuestionTopic.MEDICATION)
            elif any(w in low for w in ("eat", "food", "coffee", "drink")):
                f.question_topics.append(QuestionTopic.DIET)
            elif "normal" in low:
                f.question_topics.append(QuestionTopic.HEALING_OR_SYMPTOM)
            elif any(w in low for w in ("come in", "appointment", "reschedule", "see you")):
                f.question_topics.append(QuestionTopic.APPOINTMENT)
            else:
                f.question_topics.append(QuestionTopic.OTHER)

        if low.strip(" .!") in {"ok", "fine", "i'm doing ok", "alright", "👍", "thanks"}:
            f.is_vague_or_minimal = True
            if low.strip(" .!") == "thanks":
                f.is_vague_or_minimal = False  # polite, not a status — plain empty findings

        if any(w in low for w in ("no pain", "zero pain", "feeling great", "doing good",
                                  "all good", "no complaints", "feeling fine", "doing alright")):
            f.states_doing_well = True

        if any(w in low for w in ("he is", "her husband", "mom's", "she said")):
            f.is_proxy_report = True

        if any(w in low for w in ("chemo", "immunosuppress", "transplant")):
            f.immunocompromise_mentioned = True

        if "call me" in low or "call" in low and "doctor" in low:
            f.requests_contact = True

        if "best dental experience" in low or "absolutely wonderful" in low:
            f.unprompted_praise = True

        return f


class AlwaysGreenExtractor:
    """Pathological extractor that claims everyone is fine — used to prove
    the gate actually fails when reds land green."""

    model = "mock-always-green"
    prompt_version = "mock"
    prompt_hash = "mock-green"

    def extract(self, text, context=None) -> Findings:
        return Findings(states_doing_well=True)


class ExplodingExtractor:
    model = "mock-exploding"

    def extract(self, text, context=None) -> Findings:
        raise RuntimeError("simulated API failure")


class CountingExtractor:
    """Wraps another extractor and counts real extraction calls (cache tests)."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0
        self.model = inner.model
        self.prompt_version = inner.prompt_version
        self.prompt_hash = inner.prompt_hash

    def extract(self, text, context=None) -> Findings:
        self.calls += 1
        return self.inner.extract(text, context)
