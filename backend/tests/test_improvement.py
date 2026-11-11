"""The rule that keeps or rolls back a version, worked out by hand."""

import pytest

from backend.improvement import compare, keep_or_roll_back
from backend.models import Objectives, Scores, Thresholds

OBJECTIVES = Objectives(accuracy=0.6, grade=1.0)
NOISE = Thresholds(
    accuracy=0.1, grade=0.15, cost_per_claim=0.15, seconds_per_claim=0.15
)
TOLERANCE = Thresholds(
    accuracy=0.1, grade=0.15, cost_per_claim=1.0, seconds_per_claim=0.25
)

OLD = Scores(
    runs=2,
    accuracy=0.64,
    grade=1.5,
    cost_per_claim=0.001,
    seconds_per_claim=20.0,
)


def new(**changes: float | None) -> Scores:
    return OLD.model_copy(update=changes)


def decide(scores: Scores, old: Scores = OLD) -> tuple[bool, str]:
    return keep_or_roll_back(scores, old, OBJECTIVES, NOISE, TOLERANCE)


def test_a_version_is_kept_when_a_measure_improves_by_more_than_its_noise():
    # Accuracy 0.64 to 0.80 is 0.16 up, more than the 0.1 noise. The grade is
    # unchanged, cost the same and time the same, so nothing is worse.
    assert decide(new(accuracy=0.8)) == (True, "accuracy improved")


def test_two_measures_can_improve_together():
    # Cost down 20% (more than the 15% noise) and the grade up 0.2 (more than 0.15).
    kept = decide(new(cost_per_claim=0.0008, grade=1.7))
    assert kept == (True, "grade, cost_per_claim improved")


def test_an_improvement_inside_the_noise_is_not_enough():
    # 0.64 to 0.72 is 0.08 up, less than the 0.1 noise.
    assert decide(new(accuracy=0.72)) == (
        False,
        "no measure improved by more than the noise",
    )
    assert decide(OLD) == (False, "no measure improved by more than the noise")


def test_a_version_that_misses_the_objectives_is_rolled_back():
    assert decide(new(accuracy=0.55, grade=1.5), new(accuracy=0.4)) == (
        False,
        "it missed the objectives on accuracy",
    )
    assert decide(new(accuracy=0.9, grade=0.9)) == (
        False,
        "it missed the objectives on grade",
    )
    assert decide(new(accuracy=0.2, grade=0.5)) == (
        False,
        "it missed the objectives on accuracy, grade",
    )


def test_a_version_with_no_grade_is_rolled_back():
    assert decide(new(accuracy=0.9, grade=None)) == (False, "it has no grade")


def test_a_measure_that_gets_worse_by_more_than_its_tolerance_rolls_it_back():
    # Accuracy up 0.2, but the grade down 0.2 (tolerance 0.15).
    assert decide(new(accuracy=0.8, grade=1.3)) == (False, "grade got worse")
    # Time up 30% (tolerance 25%).
    assert decide(new(accuracy=0.8, seconds_per_claim=26.0)) == (
        False,
        "seconds_per_claim got worse",
    )


@pytest.mark.parametrize(
    ("cost", "kept"),
    [(0.002, True), (0.0020001, False)],
    ids=["exactly double", "just over double"],
)
def test_cost_is_compared_as_a_fraction_of_the_old_value(cost: float, kept: bool):
    # The tolerance on cost is 1.0, so double is allowed and a little more is not.
    assert decide(new(accuracy=0.8, cost_per_claim=cost))[0] is kept


def test_compare_lists_what_got_better_and_what_got_worse():
    better, worse = compare(
        new(accuracy=0.8, grade=1.3, seconds_per_claim=16.0), OLD, NOISE, TOLERANCE
    )
    assert better == ["accuracy", "seconds_per_claim"]
    assert worse == ["grade"]


def test_a_missing_grade_in_the_old_version_leaves_the_grade_out_of_the_comparison():
    better, worse = compare(new(accuracy=0.8), new(grade=None), NOISE, TOLERANCE)
    assert (better, worse) == (["accuracy"], [])
