Embedder `hashing`, 36 paraphrase pairs, 36 near-miss pairs (evals/semantic_pairs.jsonl). Hit = cosine >= tau AND same route.

| tau | paraphrase hit rate | false-hit rate | false-hit rate (embedder only, no route filter) |
|---:|---:|---:|---:|
| 0.50 | 83.3% | 97.2% | 100.0% |
| 0.55 | 83.3% | 91.7% | 94.4% |
| 0.60 | 83.3% | 91.7% | 94.4% |
| 0.65 | 80.6% | 83.3% | 86.1% |
| 0.70 | 77.8% | 83.3% | 86.1% |
| 0.75 | 77.8% | 72.2% | 72.2% |
| 0.80 | 75.0% | 44.4% | 44.4% |
| 0.85 | 69.4% | 27.8% | 27.8% |
| 0.90 | 50.0% | 11.1% | 11.1% |
| 0.92 | 41.7% | 2.8% | 2.8% |
| 0.95 | 27.8% | 0.0% **<- chosen** | 0.0% |
| 0.97 | 19.4% | 0.0% | 0.0% |
| 0.98 | 19.4% | 0.0% | 0.0% |
| 0.99 | 19.4% | 0.0% | 0.0% |
| 1.00 | 19.4% | 0.0% | 0.0% |

Hits per pair type at tau=0.95:

- near_miss/entity: 0/6
- near_miss/language: 0/6
- near_miss/negation: 0/6
- near_miss/number: 0/10
- near_miss/operation: 0/8
- paraphrase/case_punct: 7/8
- paraphrase/filler: 1/8
- paraphrase/reorder: 2/6
- paraphrase/synonym: 0/8
- paraphrase/typo: 0/6
