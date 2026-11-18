Workload: N=300 sequential requests, seed 7, mix {'unique': 149, 'repeat': 88, 'paraphrase': 63} (requested {'unique': 0.5, 'repeat': 0.3, 'paraphrase': 0.2}); mock LLM latency 200 ms + U(0, 50) ms; tau=0.95; gateway 1 uvicorn worker, Redis 8 in Docker, client on the same laptop (arm64).

| config | requests | LLM calls | calls avoided | cross-prompt hits* | miss p50/p95 ms | exact hit p50/p95 ms | semantic hit p50/p95 ms | all p50/p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| no-cache | 300 | 300 | 0.0% | 0 | 236.51 / 256.92 | - | - | 236.51 / 256.92 |
| exact | 300 | 164 | 45.3% | 0 | 240.23 / 261.73 | 5.27 / 7.56 | - | 218.28 / 260.24 |
| exact+semantic | 300 | 161 | 46.3% | 1 | 245.02 / 265.93 | 5.38 / 7.26 | 6.97 / 11.48 | 223.91 / 263.7 |

*answer served was cached for a different prompt; review each one:
- "good morning, have a great day!" got the answer cached for "good morning have a great day"
