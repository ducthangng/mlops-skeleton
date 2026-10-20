import json
import statistics
import urllib.request

# evals/bench.py và evals/run.py
with open("evals/prompts.jsonl") as f:
    prompts = [json.loads(line)["prompt"] for line in f]     # run.py: đổi tên biến thành data

def call(p):
    req = urllib.request.Request(
        "http://localhost:8000/route",
        data=json.dumps({"prompt": p}).encode(),
        headers={"content-type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))

for name in ["pass1", "pass2"]:
    lat, kinds = [], []
    for p in prompts:
        r = call(p)
        lat.append(r["latency_ms"])
        kinds.append(r["cache"])
    lat.sort()
    p95 = lat[int(0.95 * len(lat)) - 1]
    print(name, {k: kinds.count(k) for k in set(kinds)},
          f"p50={statistics.median(lat):.2f}ms  p95={p95:.2f}ms")