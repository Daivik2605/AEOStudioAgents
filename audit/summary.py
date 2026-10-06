"""The short plain-English numbers printed at the end of `aeo audit`. Counting only: the scored
report (with ranges) is a later step. Works from what extraction stored in businesses_named."""

from __future__ import annotations

from collections import Counter


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def build_summary(answers: list[list[dict]]) -> dict:
    """`answers` holds, per answer, the list of businesses it named.

    client_positions: the client's position in each answer that named it (best one, if listed twice).
    top_named: the three businesses named in the most answers, as (name, count). A business counts once
    per answer, and names are matched ignoring case and spaces.
    """
    client_positions: list[int] = []
    counts: Counter = Counter()
    spelling: dict[str, str] = {}
    for businesses in answers:
        in_this_answer = set()
        for b in businesses:
            key = " ".join(b["name"].lower().split())
            spelling.setdefault(key, b["name"])
            in_this_answer.add(key)
        counts.update(in_this_answer)
        positions = [b["position"] for b in businesses if b.get("is_client") and b.get("position")]
        if positions:
            client_positions.append(min(positions))

    average = round(sum(client_positions) / len(client_positions), 1) if client_positions else None
    usual = None
    if client_positions:
        tally = Counter(client_positions)
        usual = min(tally, key=lambda p: (-tally[p], p))       # most common; a tie goes to the better place
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return {"client_named": len(client_positions), "client_average_position": average,
            "client_usual_position": usual, "top_named": [(spelling[k], n) for k, n in ranked]}


def summary_lines(s: dict) -> list[str]:
    if s["client_average_position"] is None:
        first = "The business was never named with a position, so there is no average position to report."
    else:
        times = "once" if s["client_named"] == 1 else f"{s['client_named']} times"
        first = (f"Named {times}, usually {ordinal(s['client_usual_position'])} "
                 f"(average position {s['client_average_position']}).")
    if s["top_named"]:
        second = "Named most often: " + ", ".join(f"{name} x{n}" for name, n in s["top_named"]) + "."
    else:
        second = "No business was named in any answer."
    return [first, second]
