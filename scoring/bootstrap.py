"""The range around a score: a bootstrap that resamples QUESTIONS (PLAN.md section 8). Plain Python, no AI.

Why questions and not answers: the repeats of one question are not independent of each other, so
resampling answers would make the range look tighter than the data justifies. Each resample draws as many
questions as the run has, with replacement, adds up their counts, and recomputes the score. The range is the
middle 95% of those 2,000 recomputed scores.

The seed is fixed (scoring/config.yaml), so the same data always gives the same range. Only random() is used,
because Python guarantees its sequence for a given seed.
"""

from __future__ import annotations

import random
from typing import Callable

from scoring.scoring import _config

Counts = dict[str, int]
Statistic = Callable[[Counts], "float | None"]
MIN_QUESTIONS = 2          # one question cannot be resampled into anything but itself


def add_counts(units: list[Counts]) -> Counts:
    total: Counts = {}
    for unit in units:
        for key, value in unit.items():
            total[key] = total.get(key, 0) + value
    return total


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between the two nearest values (the usual definition, written out so it never changes)."""
    position = q * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def ranges(units: list[Counts], statistics: dict[str, Statistic], *, resamples: int | None = None,
           seed: int | None = None, level: float | None = None) -> dict[str, tuple[float, float] | None]:
    """One entry per statistic: (low, high), or None when there is no honest range.

    `units` holds one count dictionary per QUESTION. Each statistic turns summed counts into a score, or None
    if its denominator is zero. A statistic that is undefined in more than half the resamples gets no range.
    """
    cfg = _config()["bootstrap"]
    resamples = resamples or cfg["resamples"]
    seed = cfg["seed"] if seed is None else seed
    level = level or cfg["level"]
    if len(units) < MIN_QUESTIONS:
        return {name: None for name in statistics}

    rng = random.Random(seed)
    draw = rng.random
    k = len(units)
    keys = sorted({key for unit in units for key in unit})
    rows = [[unit.get(key, 0) for key in keys] for unit in units]
    drawn: dict[str, list[float]] = {name: [] for name in statistics}
    for _ in range(resamples):
        picked = [rows[int(draw() * k)] for _ in range(k)]          # k questions, with replacement
        summed = dict(zip(keys, (sum(column) for column in zip(*picked))))
        for name, fn in statistics.items():
            value = fn(summed)
            if value is not None:
                drawn[name].append(value)

    tail = (1 - level) / 2
    out: dict[str, tuple[float, float] | None] = {}
    for name, values in drawn.items():
        if len(values) * 2 < resamples:
            out[name] = None
            continue
        values.sort()
        out[name] = (_percentile(values, tail), _percentile(values, 1 - tail))
    return out
