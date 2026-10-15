# Crew catalog: fields and the methods they bring

Pick 3 to 5 fields plus 1 red team. Each entry lists the method to *execute* and the
artifact it returns. Never ask for a persona's voice.

## Decks, reports, memos ("get busy people to believe a causal story and decide")
| Field | Method to run | Artifact returned |
|---|---|---|
| Data analyst | Variance decomposition, cohort cuts, CIs on every headline number | Table: claim → number → CI → source query |
| Investigative journalist | Two independent sources per claim; "what would make this false" | List of single-source claims and contradictions |
| Editor | One-sentence spine; the pyramid principle; cut 40% | Spine sentence + slide titles that read as a story alone |
| Information/motion designer | One idea per frame; pre-attentive encoding; reading order | Slide-by-slide visual spec (chart type, highlight, annotation) |
| Decision scientist | Options × uncertainties; value of information | Decision table + what data would flip the call |
| **Red team: short seller / forensic accountant** | Reconcile totals across sources; hunt for the number that doesn't tie; incentive analysis | List of non-reconciling figures (>2% gap) and soft spots |

## Software / building systems ("keep it working without humans")
| Field | Method | Artifact |
|---|---|---|
| SRE | SLOs, error budgets, failure-mode enumeration | SLO doc + alerts/checks to add |
| Performance engineer | Profile first (py-spy, perf, Chrome trace), then fix the top frame | Flamegraph summary + benchmark before/after |
| Test engineer | Property-based and regression tests from real failing inputs | New tests that fail on the old code |
| Security reviewer | Threat model (STRIDE), dependency/secret scan | Ranked findings with repro |
| UX researcher | Task-completion walkthrough on the real UI (playwright) | Steps where a user gets stuck, with screenshots |
| **Red team: chaos engineer** | Kill/slow/corrupt each dependency | Which faults cause user-visible failure |

## Analysis, research, forecasting ("know something new, early, and be right")
| Field | Method | Artifact |
|---|---|---|
| Statistician | Pre-registered metric; time-split holdout; bootstrap CIs | Holdout scorecard |
| Epidemiologist | Leading indicators; survival/hazard models; base rates | Earliest-signal ranking with lead time |
| Librarian / systematic reviewer | PRISMA-style search; inclusion criteria; primary sources | Source table with quality grades |
| Domain practitioner | The checklist they use daily | Gaps between the ask and the practice |
| Superforecaster | Reference classes; explicit probabilities; update logs | Calibrated forecasts with resolution criteria |
| **Red team: replication auditor** | Re-derive the key number from raw data independently | Match/mismatch + why |

## Strategy and planning ("commit resources to the right bet")
| Field | Method | Artifact |
|---|---|---|
| Economist | Unit economics, sensitivity analysis | Sensitivity table on 3 key drivers |
| Operator | Critical path and owner per step | Gantt-lite with risks |
| Customer researcher | Jobs-to-be-done from raw transcripts/tickets | Top jobs with verbatim evidence |
| **Red team: pre-mortem facilitator / competitor strategist** | "It's 6 months later and this failed: why?" | Ranked failure causes + mitigations |

## delegate_task template (one call, parallel)
```
tasks: [
  { goal: "Apply <method> to <raw data> to answer <question>. Return <artifact>. No persona prose.",
    context: "Paths: <raw files/db>. Constraint: <target>. Output only the schema fields.",
    output_schema: { type: object, properties: { findings: {type: array}, evidence: {type: array} }, required: [findings] } },
  ...
]
```
