"""Flag builds that are slower than mean + k*sigma of the other builds in the window.

Leave-one-out: each build is compared with the *other* builds, so one very slow
build cannot hide itself by inflating sigma.
"""

import statistics
from dataclasses import dataclass


@dataclass
class SlowBuild:
    number: int
    duration_s: float
    threshold_s: float
    mean_s: float
    stdev_s: float


def slow_builds(durations: dict[int, float], k: float = 3.0, min_builds: int = 5) -> list[SlowBuild]:
    """durations: build number -> seconds (finished builds only)."""
    out = []
    if len(durations) < min_builds:
        return out
    for number, d in durations.items():
        others = [v for n, v in durations.items() if n != number]
        mean, sd = statistics.fmean(others), statistics.pstdev(others)
        threshold = mean + k * sd
        if d > threshold:
            out.append(SlowBuild(number, d, round(threshold, 1), round(mean, 1), round(sd, 1)))
    return sorted(out, key=lambda s: s.number)
