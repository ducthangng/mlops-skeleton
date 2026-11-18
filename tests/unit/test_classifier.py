import pytest

from app.classifier import RuleClassifier, make_classifier
from app.config import load_routes


def test_keyword_routes(tiny_cfg):
    d = RuleClassifier(tiny_cfg).classify("explain this stack trace")
    assert d.route == "code" and d.target["model"] == "coder"
    assert d.matched == ["stack trace"]


def test_pattern_routes(tiny_cfg):
    assert RuleClassifier(tiny_cfg).classify("what is 12 * 7").route == "math"


def test_more_matches_wins(tiny_cfg):
    # code: python + TypeError = 2 ; math: python = 1
    d = RuleClassifier(tiny_cfg).classify("python TypeError in my script")
    assert (d.route, d.score) == ("code", 2.0)


def test_tie_goes_to_first_route(tiny_cfg):
    d = RuleClassifier(tiny_cfg).classify("python")      # 1 point on both routes
    assert d.route == "code"


def test_below_min_score_falls_back_to_default(tiny_cfg):
    d = RuleClassifier(tiny_cfg).classify("tell me about the weather")
    assert d.route == "default"
    assert d.target == tiny_cfg.default


def test_keywords_match_whole_words_only(tiny_cfg):
    # 'pythonic' must not match the keyword 'python'
    assert RuleClassifier(tiny_cfg).classify("pythonic").route == "default"


def test_keywords_case_insensitive(tiny_cfg):
    assert RuleClassifier(tiny_cfg).classify("PYTHON help").route == "code"


def test_min_score_threshold(tiny_routes_path):
    cfg = load_routes(tiny_routes_path)
    strict = RuleClassifier(type(cfg)(**{**cfg.__dict__, "min_score": 2}))
    assert strict.classify("integral").route == "default"          # 1 < 2
    assert strict.classify("python TypeError").route == "code"      # 2 >= 2


def test_config_version_changes_with_content(tmp_path, tiny_routes_path):
    other = tmp_path / "other.yaml"
    other.write_text(open(tiny_routes_path).read() + "\n# edited\n")
    assert load_routes(tiny_routes_path).version != load_routes(str(other)).version


def test_real_routes_yaml_smoke():
    clf = make_classifier(load_routes("routes.yaml"), "rules")
    assert clf.classify("why does my go program deadlock").route == "code"
    assert clf.classify("solve x^2 - 4 = 0").route == "math"
    assert clf.classify("hey, tell me a joke").route == "chitchat"


def test_unknown_classifier_rejected(tiny_cfg):
    with pytest.raises(ValueError):
        make_classifier(tiny_cfg, "gpt5")
