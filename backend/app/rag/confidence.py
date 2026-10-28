"""The confidence check: decide whether the retrieved chunks can answer at all.

Specification section 9, step 5: if the top rerank score is below a threshold
calibrated on the dev set, do not answer -- say so and offer a human.

**The threshold below is measured, not chosen.** Fitted on 2026-10-28 with
``manage.py eval-retrieval --calibrate`` against ``eval/testset.yaml``: the 21
questions the plan documents should answer and the 4 written to be unanswerable,
retrieved from the real index for the hero member's plan. The sweep is
reproducible and prints what every candidate threshold would get right.

**What the measurement showed, stated plainly.** The fitted threshold of 0.016
gets 23 of 25 right overall, but almost all of that comes from correctly
answering the answerable ones. On the number that matters for safety -- a
correct "I don't know" -- it manages **2 of 4, i.e. 50%, against the project's
>= 90% target.** The threshold cannot simply be raised, because the two classes
overlap on this corpus:

* the lowest-scoring answerable question scores **0.01602**;
* the highest-scoring unanswerable one, "Will my insurance pay for LASIK eye
  surgery?", scores **0.07514** -- higher than eight of the twenty-one
  answerable questions.

The cause is that the cross-encoder scores stylistic similarity, not
answerability. A plan document says "Imaging (CT/PET scans, MRIs): 40%
coinsurance after deductible"; a member asks about a *knee* MRI. The passage
answers the question while sharing almost no wording with it. Meanwhile a
fluent glossary paragraph about eye care reads like a good match for a LASIK
question that the documents cannot actually answer.

**The identified fix, and why it is not here.** The gap is a vocabulary gap, and
verified Phase 1 data already bridges it: ``data/mappings/procedure_synonyms.csv``
maps "knee mri" to HCPCS 73721 and ``data/mappings/code_to_benefit.csv`` maps
73721 to the benefit category a plan prices. Resolving a question's service
through those tables would separate "knee MRI" (a known service in a priced
category) from "LASIK" (no such service in any of our documents). The same
mapping is what the three retrieval misses need -- "physiotherapy" against a
plan that says "Rehabilitation services". That belongs with the answering step
and the router-first tool pre-fetch, which the specification puts in Phase 3, so
it is scheduled there rather than half-built here.

Until then this module reports its real accuracy, errs toward declining, and the
gateway offers a human whenever it declines. A false refusal costs the member a
handoff; a confident wrong answer about their coverage costs them money.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.rag.retrieve import Retrieved

#: Minimum top rerank score for the retrieved set to be considered answerable.
#: See the module docstring for how this was measured and what it does not do.
CONFIDENCE_THRESHOLD = 0.016

#: When the threshold was last fitted, and against what.
CALIBRATED_ON = dt.date(2026, 10, 28)
CALIBRATION_SET = (
    "eval/testset.yaml: 21 document-answerable questions + 4 written to be unanswerable, "
    "retrieved for member M10234's plan (47501TX0040002) from index chunker v4"
)
CALIBRATION_COMMAND = "uv run python manage.py eval-retrieval --calibrate"
CALIBRATION_ACCURACY = (
    '23/25 correct overall; 21/21 answerable answered; correct "I don\'t know" 2/4 = 50%, '
    "against a >= 90% target. The classes overlap on this corpus, so the threshold cannot be "
    "raised to close the gap -- see the module docstring for the cause and the Phase 3 fix."
)

#: What a member is told when the documents cannot answer. The gateway always
#: pairs this with the offer of a human.
NOT_FOUND_MESSAGE = "I couldn't find this in your plan documents."


@dataclass(frozen=True)
class ConfidenceVerdict:
    """Whether a retrieved set may be used to answer.

    Attributes:
        answerable: True when the answer may be generated from these chunks.
        top_score: The top rerank score that decided it.
        threshold: The threshold in force.
        chunk_count: How many chunks were retrieved.
    """

    answerable: bool
    top_score: float
    threshold: float
    chunk_count: int

    def explain(self) -> str:
        """One line for the console and for the Security view's pipeline trace."""
        if self.chunk_count == 0:
            return "No chunks retrieved, so there is nothing to answer from."
        verb = "at or above" if self.answerable else "below"
        decision = "answerable" if self.answerable else "not answerable — offer a human"
        return (
            f"Top rerank {self.top_score:.4f} is {verb} the calibrated threshold "
            f"{self.threshold:.4f} (fitted {CALIBRATED_ON.isoformat()}): {decision}."
        )


def confidence_verdict(
    results: list[Retrieved], *, threshold: float = CONFIDENCE_THRESHOLD
) -> ConfidenceVerdict:
    """Judge whether a retrieved set is good enough to answer from.

    Args:
        results: Reranked chunks, best first, as ``retrieve`` returns them.
        threshold: Override for calibration runs; defaults to the fitted value.

    Returns:
        The verdict, carrying the numbers behind it so the decision is auditable
        rather than opaque.
    """
    if not results:
        return ConfidenceVerdict(
            answerable=False, top_score=0.0, threshold=threshold, chunk_count=0
        )
    top = max(result.rerank_score or 0.0 for result in results)
    return ConfidenceVerdict(
        answerable=top >= threshold,
        top_score=top,
        threshold=threshold,
        chunk_count=len(results),
    )
