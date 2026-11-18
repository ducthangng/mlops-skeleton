from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from tools.logtriage.classify import classify
from tools.logtriage.cli import app
from tools.logtriage.jenkins import Jenkins, job_path
from tools.logtriage.stats import slow_builds

SAMPLES = Path("tools/logtriage/samples")


@pytest.mark.parametrize("name, category, needle", [
    ("lint_synthetic.log", "lint", "F401"),
    ("test_synthetic.log", "test", "FAILED tests/unit/test_classifier.py"),
    ("oom_synthetic.log", "oom", "OOMKilled"),
    ("auth_synthetic.log", "auth", "Authentication failed"),
    ("image_pull_synthetic.log", "image_pull", "manifest unknown"),
    ("timeout_during_tests_synthetic.log", "timeout", "Timeout has been exceeded"),
    ("unknown_git_not_found.log", "unknown", "git: not found"),
    # real: agent pod stuck in ContainerCreating (missing ghcr-creds Secret), then aborted.
    # The console never names the Secret; the evidence is the Pending pod line.
    ("real_aborted_pod_pending.log", "unknown", "Pod [Pending]"),
])
def test_classify_samples(name, category, needle):
    t = classify((SAMPLES / name).read_text())
    assert t.category == category
    assert needle in t.evidence.line
    assert t.evidence.line_no >= 1


def test_priority_timeout_beats_test_but_test_is_reported():
    t = classify((SAMPLES / "timeout_during_tests_synthetic.log").read_text())
    assert t.category == "timeout" and "test" in t.also


def test_evidence_line_number_points_at_the_line():
    log = "a\nb\nOOMKilled here\n"
    assert classify(log).evidence.line_no == 3


def test_empty_log():
    assert classify("").category == "unknown"


def test_slow_builds_leave_one_out():
    durations = {i: 60.0 + (i % 3) for i in range(1, 20)}
    durations[20] = 300.0
    slow = slow_builds(durations)
    assert [s.number for s in slow] == [20]
    assert slow[0].threshold_s < 70


def test_slow_builds_needs_enough_history():
    assert slow_builds({1: 10.0, 2: 500.0}) == []


def test_job_path_encodes_branches():
    assert job_path("router", "main") == "/job/router/job/main"
    assert job_path("router", "feat/cv-parity") == "/job/router/job/feat%252Fcv-parity"
    assert job_path("qualify") == "/job/qualify"


def fake_jenkins(monkeypatch):
    builds = [{"number": n, "result": "SUCCESS", "duration": 60_000, "timestamp": 0, "url": ""} for n in range(2, 12)]
    builds.insert(0, {"number": 12, "result": "FAILURE", "duration": 900_000, "timestamp": 0, "url": ""})

    def handler(req: httpx.Request):
        if req.url.path.endswith("/api/json"):
            return httpx.Response(200, json={"builds": builds})
        return httpx.Response(200, text=(SAMPLES / "oom_synthetic.log").read_text())

    real_init = Jenkins.__init__

    def init(self, url, user=None, token=None, timeout_s=20):
        real_init(self, url, user, token, timeout_s)
        self.http = httpx.Client(base_url=url, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(Jenkins, "__init__", init)


def test_cli_jenkins_report(monkeypatch, tmp_path):
    fake_jenkins(monkeypatch)
    res = CliRunner().invoke(app, ["jenkins", "--url", "http://j", "--out", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "oom" in res.output and "**SLOW**" in res.output
    assert (tmp_path / "triage.json").exists() and (tmp_path / "triage.md").exists()


def test_cli_analyze(tmp_path):
    res = CliRunner().invoke(app, ["analyze", str(SAMPLES / "lint_synthetic.log"), "--out", str(tmp_path)])
    assert res.exit_code == 0 and "lint" in res.output
