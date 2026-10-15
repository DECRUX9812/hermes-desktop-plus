# Outside judges by task type

Rule: the judge is chosen and its pass bar written down **before** the work starts.
The authoring model never grades itself.

| Task type | Preferred judge | Concrete form |
|---|---|---|
| Bug fix / reliability | Reproduction + regression test | Failing repro script → passes; `pytest -q tests/test_x.py`; error rate from real logs before/after |
| Performance | Benchmark harness | `hyperfine`, `pytest-benchmark`, k6/locust; report p50/p95 vs target |
| Feature / UI | Automated E2E on the real app | Playwright script that performs the task, reloads, asserts persisted state |
| Code quality | Static tools | `ruff`, `mypy`/`tsc --noEmit`, coverage delta |
| Data analysis / prediction | Held-out data | Time split: fit on ≤T, score on >T; pre-registered metric (AUC, precision@k, MAE) vs a naive baseline |
| Research claims | Primary sources | Each key claim links to the primary document; a subagent re-finds it independently |
| Forecasts | Resolution criteria + scoring | Brier/log score once resolved; until then, a reference-class baseline |
| Deck / memo / writing | Different-model blind reviewer + comprehension test | Cheap/free model that differs from the author gets the rubric first, then the artifact; asks "after slide 1, what's the decision?" and compares to the intended spine |
| Plans / strategy | Pre-mortem by a separate subagent + checkable milestones | Each milestone has a date and an observable signal |

## Different-model reviewer recipe
1. Write the rubric (3–6 binary or 1–5 criteria) before the work exists.
2. Send it to a subagent (or a free model that differs from the author) with the
   artifact, unlabeled, ideally next to a baseline version (the literal-ask version).
3. Ask for scores plus the single weakest point. Take the weakest point seriously.
4. Report the scores verbatim, without paraphrasing them upward.

## When no outside judge exists
Say so on the card ("Judge: none available, so confidence is low"), and offer the
cheapest judge the user could enable (a sample of labeled data, a staging env, a
reviewer).
