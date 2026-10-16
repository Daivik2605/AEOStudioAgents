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


# ---- paired bootstrap, for comparing two runs (PLAN.md section 8, `aeo compare`) ----------------------------------------
#
# The same questions were asked in both runs, so a resample draws a set of QUESTIONS and uses that same set for run A
# and for run B. The change is then measured on identical questions, which is what makes the range honest (and narrower
# than treating the two runs as strangers). `units_a[i]` and `units_b[i]` are the counts for question i in each run.
# Same seed as the audit report, so the same data always gives the same range.

Interval = "tuple[float, float] | None"


def _interval(values: list[float], resamples: int, level: float):
    if len(values) * 2 < resamples:           # undefined in over half the resamples: no honest range
        return None
    values.sort()
    tail = (1 - level) / 2
    return (_percentile(values, tail), _percentile(values, 1 - tail))


def _grid(units_a: list[Counts], units_b: list[Counts]):
    if len(units_a) != len(units_b):
        raise ValueError("paired units must line up question by question")
    keys = sorted({key for unit in units_a + units_b for key in unit})
    return (keys, [[u.get(k, 0) for k in keys] for u in units_a], [[u.get(k, 0) for k in keys] for u in units_b])


def _draw(draw, grid) -> tuple[Counts, Counts]:
    """One resample of one group of questions: the same drawn questions summed for A and for B."""
    keys, rows_a, rows_b = grid
    k = len(rows_a)
    picked = [int(draw() * k) for _ in range(k)]
    sum_a = dict(zip(keys, (sum(rows_a[i][j] for i in picked) for j in range(len(keys)))))
    sum_b = dict(zip(keys, (sum(rows_b[i][j] for i in picked) for j in range(len(keys)))))
    return sum_a, sum_b


def _change(fn: Statistic, sum_a: Counts, sum_b: Counts):
    a, b = fn(sum_a), fn(sum_b)
    return None if a is None or b is None else b - a


def paired_ranges(units_a: list[Counts], units_b: list[Counts], statistics: dict[str, Statistic], *,
                  resamples: int | None = None, seed: int | None = None, level: float | None = None
                  ) -> dict[str, "tuple[float, float] | None"]:
    """The range of (B - A) for each statistic, resampling questions in pairs."""
    cfg = _config()["bootstrap"]
    resamples = resamples or cfg["resamples"]
    seed = cfg["seed"] if seed is None else seed
    level = level or cfg["level"]
    if len(units_a) < MIN_QUESTIONS:
        return {name: None for name in statistics}
    grid = _grid(units_a, units_b)
    rng = random.Random(seed)
    drawn: dict[str, list[float]] = {name: [] for name in statistics}
    for _ in range(resamples):
        sum_a, sum_b = _draw(rng.random, grid)
        for name, fn in statistics.items():
            change = _change(fn, sum_a, sum_b)
            if change is not None:
                drawn[name].append(change)
    return {name: _interval(values, resamples, level) for name, values in drawn.items()}


def paired_difference_ranges(target: tuple[list[Counts], list[Counts]], control: tuple[list[Counts], list[Counts]],
                             statistics: dict[str, Statistic], *, resamples: int | None = None,
                             seed: int | None = None, level: float | None = None) -> dict[str, dict]:
    """For each statistic: the range of the change on target questions, of the change on control questions, and of
    the DIFFERENCE between the two (target change minus control change). Targets and control are resampled
    separately, so each keeps its own number of questions."""
    cfg = _config()["bootstrap"]
    resamples = resamples or cfg["resamples"]
    seed = cfg["seed"] if seed is None else seed
    level = level or cfg["level"]
    none = {name: {"target": None, "control": None, "difference": None} for name in statistics}
    if len(target[0]) < MIN_QUESTIONS or len(control[0]) < MIN_QUESTIONS:
        return none
    grid_t, grid_c = _grid(*target), _grid(*control)
    rng = random.Random(seed)
    drawn = {name: {"target": [], "control": [], "difference": []} for name in statistics}
    for _ in range(resamples):
        t_a, t_b = _draw(rng.random, grid_t)
        c_a, c_b = _draw(rng.random, grid_c)
        for name, fn in statistics.items():
            ct, cc = _change(fn, t_a, t_b), _change(fn, c_a, c_b)
            if ct is not None:
                drawn[name]["target"].append(ct)
            if cc is not None:
                drawn[name]["control"].append(cc)
            if ct is not None and cc is not None:
                drawn[name]["difference"].append(ct - cc)
    return {name: {part: _interval(vals, resamples, level) for part, vals in parts.items()}
            for name, parts in drawn.items()}
