"""Minimal Jenkins REST client (read-only: builds list + consoleText)."""

from dataclasses import dataclass
from urllib.parse import quote

import httpx


@dataclass
class Build:
    number: int
    result: str | None        # SUCCESS | FAILURE | ABORTED | UNSTABLE | None (running)
    duration_s: float
    timestamp_ms: int
    url: str


def job_path(job: str, branch: str | None = None) -> str:
    """job 'ai-gateway', branch 'feat/x' -> '/job/ai-gateway/job/feat%252Fx'.

    Multibranch item names encode '/' as %2F, and the URL then encodes '%' again."""
    path = "".join(f"/job/{quote(part, safe='')}" for part in job.split("/"))
    return path + (f"/job/{quote(quote(branch, safe=''), safe='')}" if branch else "")


class Jenkins:
    def __init__(self, url: str, user: str | None = None, token: str | None = None, timeout_s: float = 20):
        auth = (user, token) if user and token else None
        self.http = httpx.Client(base_url=url.rstrip("/"), auth=auth, timeout=timeout_s)

    def builds(self, job: str, branch: str | None = None, last: int = 20) -> list[Build]:
        tree = f"builds[number,result,duration,timestamp,url]{{0,{last}}}"
        r = self.http.get(f"{job_path(job, branch)}/api/json", params={"tree": tree})
        r.raise_for_status()
        return [Build(b["number"], b["result"], b["duration"] / 1000, b["timestamp"], b["url"])
                for b in r.json()["builds"]]

    def console(self, job: str, number: int, branch: str | None = None) -> str:
        r = self.http.get(f"{job_path(job, branch)}/{number}/consoleText")
        r.raise_for_status()
        return r.text
