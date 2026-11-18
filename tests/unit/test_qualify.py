import json
from pathlib import Path

from qualify.compare import compare
from qualify.run import inputs_key, load_ctx, run_stage
from tools.logtriage.qualify_diff import diff_run


def test_compare_only_reports_new_failures_and_regressions():
    golden = {"metrics": {"p95_ms": 20, "accuracy": 0.97}, "failures": [{"id": "known"}],
              "compare": {"p95_ms": {"better": "lower", "rel": 1.0, "abs": 25},
                          "accuracy": {"better": "higher", "abs": 0.02}}}
    run = {"metrics": {"p95_ms": 60, "accuracy": 0.96},
           "failures": [{"id": "known"}, {"id": "new", "evidence": "x"}]}
    d = compare(run, golden)
    assert [f["id"] for f in d["new_failures"]] == ["new"]
    assert d["regressed_metrics"] == []                        # 60 <= 20*2+25 ; 0.96 >= 0.95
    d = compare({**run, "metrics": {"p95_ms": 66, "accuracy": 0.94}}, golden)
    assert {m["metric"] for m in d["regressed_metrics"]} == {"p95_ms", "accuracy"}


def test_scenarios_overlay_routes():
    assert load_ctx("clean", []).routes_path == "routes.yaml"
    assert load_ctx("bad", []).routes_path.endswith("scenarios/bad/routes.yaml")
    assert load_ctx("clean", ["SEMANTIC_CACHE_THRESHOLD=0.8"]).env == {"SEMANTIC_CACHE_THRESHOLD": "0.8"}


def test_inputs_key_changes_with_inputs():
    clean, partial = load_ctx("clean", []), load_ctx("partial", [])
    assert inputs_key("routing", clean) == inputs_key("routing", clean)
    assert inputs_key("routing", clean) != inputs_key("routing", partial)     # routes differ
    assert inputs_key("contract", clean) == inputs_key("contract", partial)   # contract ignores routes
    assert inputs_key("cache", clean) != inputs_key("cache", load_ctx("clean", ["SEMANTIC_CACHE_THRESHOLD=0.5"]))


def test_bad_scenario_fails_config_lint_with_evidence(tmp_path):
    s = run_stage("config_lint", load_ctx("bad", []), tmp_path, None, "bad")
    assert s["status"] == "fail"
    msgs = " ".join(f["message"] for f in s["failures"])
    assert "not a string" in msgs and "does not compile" in msgs
    assert (tmp_path / "config_lint.log").read_text().startswith("FAIL")


def test_clean_routing_passes_and_is_cached(tmp_path):
    cache = tmp_path / "cache"
    first = run_stage("routing", load_ctx("clean", []), tmp_path / "a", cache, "clean")
    second = run_stage("routing", load_ctx("clean", []), tmp_path / "b", cache, "clean")
    assert first["status"] == "pass" and not first["skipped"]
    assert second["skipped"] == "cache"


def test_low_tau_is_a_cache_regression(tmp_path):
    s = run_stage("cache", load_ctx("clean", ["SEMANTIC_CACHE_THRESHOLD=0.85"]), tmp_path, None, "clean")
    assert s["status"] == "fail" and s["metrics"]["false_hit_rate"] > 0
    assert any("false hit" in f["message"] for f in s["failures"])


def test_diff_run_reports_not_run_stages(tmp_path):
    run_stage("config_lint", load_ctx("bad", []), tmp_path, None, "bad")
    report, md = diff_run(tmp_path, Path("qualify/golden"))
    assert report["not_run"] == ["contract", "routing", "cache", "load"]
    assert len(report["regressions"]) == 2 and "config_lint.log:1" in md
    json.dumps(report)
