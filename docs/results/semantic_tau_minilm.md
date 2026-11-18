Embedder `minilm`, 36 paraphrase pairs, 36 near-miss pairs (evals/semantic_pairs.jsonl). Hit = cosine >= tau AND same route.

| tau | paraphrase hit rate | false-hit rate | false-hit rate (embedder only, no route filter) |
|---:|---:|---:|---:|
| 0.50 | 94.4% | 94.4% | 97.2% |
| 0.55 | 91.7% | 94.4% | 97.2% |
| 0.60 | 88.9% | 91.7% | 94.4% |
| 0.65 | 88.9% | 91.7% | 91.7% |
| 0.70 | 88.9% | 80.6% | 80.6% |
| 0.75 | 86.1% | 69.4% | 69.4% |
| 0.80 | 86.1% | 52.8% | 52.8% |
| 0.85 | 77.8% | 33.3% | 33.3% |
| 0.90 | 66.7% | 19.4% | 19.4% |
| 0.92 | 66.7% | 16.7% | 16.7% |
| 0.95 | 55.6% | 2.8% | 2.8% |
| 0.97 | 52.8% | 2.8% | 2.8% |
| 0.98 | 52.8% | 2.8% | 2.8% |
| 0.99 | 33.3% | 2.8% | 2.8% |
| 1.00 | 0.0% | 0.0% **<- chosen** | 0.0% |

Hits per pair type at tau=1.00:

- near_miss/entity: 0/6
- near_miss/language: 0/6
- near_miss/negation: 0/6
- near_miss/number: 0/10
- near_miss/operation: 0/8
- paraphrase/case_punct: 0/8
- paraphrase/filler: 0/8
- paraphrase/reorder: 0/6
- paraphrase/synonym: 0/8
- paraphrase/typo: 0/6
