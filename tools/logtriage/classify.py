"""Classify a CI console log into a failure category, with the evidence line.

Rules are ordered by priority: when several categories match (e.g. a timeout that
interrupted the tests), the first category in RULES is the primary cause.
"""

import re
from dataclasses import asdict, dataclass, field

RULES: list[tuple[str, list[str]]] = [
    ("oom", [
        r"OOMKilled",
        r"exit code 137\b",
        r"Exit Code: 137",
        r"\bMemoryError\b",
        r"Cannot allocate memory",
    ]),
    ("image_pull", [
        r"ImagePullBackOff",
        r"ErrImagePull",
        r"manifest unknown",
        r"pull access denied",
    ]),
    ("auth", [
        r"\b401 Unauthorized\b",
        r"\b403 Forbidden\b",
        r"UNAUTHORIZED",
        r"authentication required",
        r"Authentication failed",
        r"could not read Username",
        r"denied: ",
        r'secret "[^"]+" not found',
        r"Could not find credentials entry with ID",
    ]),
    ("timeout", [
        r"Timeout has been exceeded",
        r"Cancelling nested steps due to timeout",
        r"\btimed out\b",
        r"deadline exceeded",
    ]),
    ("lint", [
        r"^\S+\.py:\d+:\d+: [A-Z]+\d+ ",        # ruff concise format
        r"^Found \d+ errors?\.",
        r"would reformat",
    ]),
    ("test", [
        r"^FAILED tests/",
        r"^=+ .*\b\d+ failed\b",
        r"Coverage failure: total of",
        r"FAIL Required test coverage",
        r"^gate: .* -> FAIL",
        r"^ERROR tests/",
    ]),
]
COMPILED = [(cat, [re.compile(p) for p in pats]) for cat, pats in RULES]
FAILURE_HINT = re.compile(r"\b(ERROR|FAILURE|Error|error:|failed|not found|No such file|Pending)\b")
# Jenkins wrapper lines repeat the exit code but never explain it: useless as evidence.
WRAPPER = re.compile(r"script returned exit code|^Finished:|^\[Pipeline\]")


@dataclass
class Evidence:
    line_no: int          # 1-based
    line: str
    rule: str


@dataclass
class Triage:
    category: str                                    # primary cause
    evidence: Evidence | None
    also: dict[str, Evidence] = field(default_factory=dict)   # other categories that matched

    def as_dict(self) -> dict:
        return asdict(self)


def classify(log: str) -> Triage:
    first_hit: dict[str, Evidence] = {}
    lines = log.splitlines()
    for no, line in enumerate(lines, 1):
        for cat, pats in COMPILED:
            if cat in first_hit:
                continue
            for rx in pats:
                if rx.search(line):
                    first_hit[cat] = Evidence(no, line.strip()[:300], rx.pattern)
                    break
    for cat, _ in RULES:                             # priority order
        if cat in first_hit:
            others = {c: e for c, e in first_hit.items() if c != cat}
            return Triage(cat, first_hit[cat], others)
    # unknown: the FIRST explicit "ERROR:" line is usually the root cause (later ones are consequences,
    # e.g. post actions failing because the pod never started); else the last error-like line.
    for no, line in enumerate(lines, 1):
        if line.startswith("ERROR:") and not WRAPPER.search(line):
            return Triage("unknown", Evidence(no, line.strip()[:300], "^ERROR:"))
    for no in range(len(lines), 0, -1):
        if FAILURE_HINT.search(lines[no - 1]) and not WRAPPER.search(lines[no - 1]):
            return Triage("unknown", Evidence(no, lines[no - 1].strip()[:300], FAILURE_HINT.pattern))
    tail = next(((n, ln) for n, ln in reversed(list(enumerate(lines, 1))) if ln.strip()), None)
    return Triage("unknown", Evidence(tail[0], tail[1].strip(), "") if tail else None)
