import json
from collections import Counter

from app.router import SemanticRouter

r = SemanticRouter("sentence-transformers/all-MiniLM-L6-v2", "routes.yaml")
with open("evals/prompts.jsonl") as f:
    data = [json.loads(line)["prompt"] for line in f]     # run.py: đổi tên biến thành data

for strategy in ["centroid", "max"]:
    ok, errors = 0, Counter()
    for d in data:
        pred = r.route(d["prompt"], strategy)["route"]
        ok += pred == d["label"]
        if pred != d["label"]:
            errors[f'{d["label"]}→{pred}'] += 1
    print(f"{strategy:8s} acc={ok/len(data):.1%}  errors={errors.most_common(4)}")