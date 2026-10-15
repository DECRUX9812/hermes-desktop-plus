---
name: ambition
description: "Be ambitious: upgrade big asks (altitude/depth/range/judge)"
version: 1.0.0
author: Hermes Team (adapted from "How to be more ambitious with AI")
license: MIT
metadata:
  hermes:
    tags: [planning, research, analysis, strategy, decks, quality, evaluation, delegation]
    category: productivity
    related_skills: [brainstorming, dispatching-parallel-agents]
---

# Ambition: upgrade the ask before you execute it

The usual failure is not a wrong answer. It is a correct answer to a small question.
This skill makes the agent stop and upgrade the request along three axes (Altitude,
Depth and Range), set a hard target, and name an outside judge before doing the work.
The fourth type of ambition, Speed, is what you get for free once the other three are
set up right. The idea comes from the essay "How to be more ambitious with AI".

## When to Use

Use it when any of these is true:
- The user says "be ambitious", "go deeper", "ambition mode", or starts with `/ambition`.
- The task is substantial: planning, building a feature or system, data analysis,
  research or a literature review, strategy, a deck or report, an investigation or
  debugging a recurring problem, or a forecast.
- The request names an artifact (a deck, a table, a fix, a summary) when the real need
  is an outcome.

Skip it (or do a one-line version) when:
- The ask is trivial or mechanical: a rename, a typo, a lookup, a one-line answer,
  a format conversion, a quick factual question. Never inflate these.
- The user said "just do X", "quick", or "don't overthink", or the task is mid-run and
  already scoped.
- It is an emergency (prod down, a deadline in minutes). Fix it first and offer the
  Altitude upgrade afterwards.

## Procedure

### 0. Size check (5 seconds)
Ask yourself: would a top professional do something materially different from the
literal ask? If not, skip the skill and just do the task well. If yes, continue.
Scale the effort: a 1-hour task gets a 6-line upgrade, and a multi-day project gets the
full card.

### 1. Altitude: find the goal behind the goal
- Go up one or two levels of abstraction to the outcome the user is paying for.
  "Fix this crash" becomes "keep the apps crash-free". "Summarize these tickets"
  becomes "know which customers are about to churn and why".
- Ask: if this succeeds, what changes? What will the user do next with it? What
  recurring job does this belong to?
- Prefer **durable workflows over one-off answers**: a check, a cron routine, a test,
  a dashboard, a script, or a skill that keeps the outcome true without a human in the
  loop. Propose it, but build it only if it fits the user's scope (see Guardrails).
- List the **access the agent needs** to own the outcome: data sources, logs, a repro
  environment, credentials *the user grants*, a sandbox or a staging copy. Missing
  access goes into the plan as an explicit ask. Never route around it.

### 2. Depth: run the depth meta-prompt on the task
Answer these four questions in your scratch reasoning (put the answers on the card in
short form):

> I want a deeper version of this task, not a bigger one. Before rewriting it:
> (1) say what a top professional in this field would do here that I'm not asking for;
> (2) name the rawest material they would start from instead of what has already been
> digested for me; (3) name the specific methods and tools they would use; (4) say what,
> other than you or me, would show the result is right. Then rewrite my prompt to ask
> for exactly that.

Then apply these depth rules:
- **Professional instruments, not toy ones.** Use SQL/DuckDB/SQLite, pandas or polars,
  statistical models (regression, survival, changepoint, bootstrap CIs), profilers,
  tracers, benchmarks, property tests, and real browsers. A markdown table is a
  display format, not an analysis method. Run the code; don't simulate it in prose.
- **Raw material first.** Raw logs, event tables, source code, primary filings,
  original papers, and transcripts beat dashboards, summaries and someone else's slide.
  Note where the digested version disagrees with the raw data.
- **Unreasonable but concrete targets.** "p95 < 200 ms", not "faster". "Catch 80% of
  churners 30 days early at 1 false alarm in 5", not "find churn signals". Insist the
  target is solvable and search for the approach that gets there before you give up.
- **Adversarial assumption check.** Write the 3 to 5 assumptions the plan rests on and
  test each one against raw data before building on it.
- **Produce new knowledge, not just a report.** Aim for blind spots, earliest
  signals, a causal driver, or a counter-intuitive finding. Validate it on
  **held-out data** (time split or a holdout set), not on the data you explored.

### 3. Range: turn nouns into verbs and assemble a crew of fields
- Restate the artifact as the job it does. A QBR deck is "getting busy people to believe
  a cause-and-effect story and decide under uncertainty in 30 minutes". A PRD is
  "getting engineers to build the right thing without coming back with questions".
- Pick 3 to 5 **fields** whose *methods* fit that verb, plus one **red team**. See
  `references/crew-catalog.md` for a menu. For example: data analyst (cohort and
  variance decomposition), investigative journalist (source triangulation and the
  "who benefits" question), editor (one-sentence spine, cutting 40%), motion or
  information designer (pre-attentive encoding, one idea per frame), and as red team a
  short seller or forensic accountant (find the number that doesn't reconcile).
- **Methods, not role play.** Each crew member returns *the output of applying a
  method*: a reconciliation table, a list of claims without a second source, a
  cut-down outline. They never return prose "in the voice of" a short seller. "Act
  as a short seller and write a memo" is wrong. "Do what a short seller does: reconcile
  the reported numbers against the raw ledger and list every gap over 2%" is right.
- **Run the crew as subagents** with `delegate_task` (independent `tasks` in one call,
  run in parallel), each with a self-contained `goal`, the raw-data paths in `context`,
  and an `output_schema` with only the fields you will read. Subagents run on the
  configured `delegation.model`, which is already a cheap model on this install, so do
  not ask for a premium model for crew work. If delegation is unavailable, apply the
  methods yourself sequentially, one field at a time, and label each pass.

### 4. Target and Outside Judge (required, never skipped)
- **Target**: one or two numbers or binary pass conditions, stated before the work
  starts (latency, accuracy on holdout, test pass rate, coverage, error budget, "the
  exec can state the decision in 1 sentence after reading slide 1").
- **Judge**: something *other than this model's own opinion* decides whether the target
  was hit. In order of preference (see `references/judges.md`):
  1. A tool that runs: a test suite, a benchmark harness, a type checker, a linter, a
     reproduction script, a load test, `hyperfine`, `pytest`, or playwright against the
     real app.
  2. Held-out data: a time-split or holdout set that was not touched during
     exploration, with a pre-registered metric.
  3. A real-world check: the live API, an independent dataset, or a primary source.
  4. A different-model reviewer with a rubric fixed *before* it sees the work (a
     subagent or a cheap/free model that differs from the author model), blind to which
     version is "ours".
  5. The user, as the last resort, with a concrete accept/reject question.
- **Never self-grade.** "I reviewed it and it looks good" is not a judgment. If no
  outside judge is possible, say so explicitly on the card and lower the confidence
  claim.
- **Permission to fail.** Tell the user (and the crew) that missing the hard target is
  acceptable if it's reported honestly with the measured number. Do not quietly lower
  the target or redefine success after the fact.

### 5. Show the ambitious version, then proceed
Show the user a short card before executing. Keep it under about 12 lines:

```
**Ambitious version**
- Ask → Outcome (Altitude): <literal ask> → <goal behind the goal>; durable piece: <workflow/check/routine>
- Depth: start from <raw material>; instruments <tools/methods>; assumptions to test <2-3>
- Range: verb = "<noun as a verb>"; crew = <field: method> × 3-5 + red team <field: method>
- Target: <hard number or pass condition> (permission to miss; we'll report the real number)
- Judge: <tool/test/holdout/other-model reviewer>, not self-assessment
- Needs from you: <access/data/approvals>, or "nothing"
Proceeding with this unless you want changes. (Reply "smaller" to do just the literal ask.)
```

Then:
- If the plan stays within what the user asked for (same deliverable, done more
  deeply) and needs no new access, **proceed right away**. Don't block on a reply.
- If the upgrade widens scope materially (new systems, recurring jobs, spending
  money, contacting people, touching prod) or needs new access, **ask first** and do
  only the literal ask plus the read-only depth work until the user says yes.

### 6. Execute, judge and report
- Execute with the instruments named. Track sub-steps with the todo tool for longer
  runs.
- Run the judge. Report **Target vs. measured** in one line, plus the judge's verdict
  and evidence (command output, holdout metric, reviewer rubric score).
- Report what was learned that wasn't asked for (blind spots, earliest signals), and
  what durable piece now exists or is proposed.
- If the target was missed: give the measured number, the best next approach, and what
  it would take.

## Guardrails
- **Don't inflate trivial asks.** The size check is mandatory. Ambition means depth,
  not volume. A longer deliverable is not a deeper one.
- **Ask before anything consequential.** Sending, posting, purchasing, deleting,
  deploying, schedules or cron jobs, permission or credential changes, and anything
  touching prod or other people all need explicit approval, even when the
  "durable workflow" idea calls for them. Propose them; don't do them.
- **No role-play fluff.** Crew members are method carriers. Strip personas, catch
  phrases and "as a seasoned journalist..." framing from outputs.
- **Keep costs low.** Run the crew on the cheap `delegation.model` and keep it to 3–5
  members plus 1 red team at most. Prefer local tools (DuckDB, scripts) over model
  calls. Never pick a paid or premium model just for crew or judge work; for a
  different-model reviewer, pick a free or cheap model that differs from the author.
  One crew round by default, and a second only if the judge fails and a clear fix exists.
- **Honest limits.** If raw data or access isn't available, say so and give the best
  result with the digested inputs, labeled as such.
- **Respect user scope.** The user's literal ask is always delivered. The ambitious
  version extends it and never replaces it without consent.

## Pitfalls
- Writing the card and then executing the original small plan anyway. The card is a
  contract, so execute it.
- A "target" that isn't measurable ("high quality", "comprehensive").
- A judge that is the same model rereading its own work, or a rubric written after
  seeing the output.
- Tuning on the holdout set. Once it has been used to choose, it is no longer held out.
  Keep a final untouched slice.
- Crew outputs that are opinions instead of artifacts. Demand tables, diffs, lists of
  failing cases and numbers.
- Over-scoping a 20-minute ask into a week-long program. Offer the big version as a
  follow-up instead.

## Verification
The skill was applied correctly when:
- The card (or the 1-line version for small tasks) appeared before execution, with all
  of Altitude, Depth, Range, Target and Judge filled in.
- The final report contains "Target: X / Measured: Y / Judge: Z" with evidence from an
  outside judge, not self-assessment.
- No consequential action happened without explicit approval.

## References
- `references/crew-catalog.md`: fields → methods menu, by task type.
- `references/judges.md`: outside-judge patterns by task type, with concrete commands.
