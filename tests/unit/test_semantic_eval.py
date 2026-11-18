from evals.semantic_eval import pick_tau, score_pairs, table


def test_table_and_pick_tau_on_toy_pairs():
    pairs = [
        {"kind": "paraphrase", "type": "case", "a": "why does my go program deadlock",
         "b": "Why does my Go program deadlock?"},
        {"kind": "near_miss", "type": "number", "a": "what is the derivative of x^2",
         "b": "what is the derivative of x^3"},
    ]
    scored = score_pairs(pairs)
    rows = table(scored, taus=[0.5, 0.99])
    assert rows[0]["false_hit_rate"] == 1.0          # tau 0.5: the near miss hits
    chosen = pick_tau(rows, max_false_hit=0.0)
    assert chosen["tau"] == 0.99 and chosen["paraphrase_hit_rate"] == 1.0


def test_pick_tau_none_when_impossible():
    assert pick_tau([{"tau": 0.9, "false_hit_rate": 0.5}], 0.0) is None
