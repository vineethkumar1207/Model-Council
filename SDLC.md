# Model Council --- SDLC.md {#model-council--sdlcmd}

> **Role:** Controlled software-development lifecycle and implementation
> contract. **Purpose:** Safely repair, extend, verify and promote Model
> Council without uncontrolled rewrites.

## 1. Development objective {#1-development-objective}

Model Council must be developed through staged, harness-first delivery.
The goal is verified product progress, not code volume or model-call
volume.

``` text
Contract → Baseline → Minimal Change → Deterministic Verification → Integration → Real-Model Verification → Promotion
```

Never use:

``` text
Broad rewrite → many model calls → looks good → promotion
```

## 2. Governing principles {#2-governing-principles}

-   **Harness first:** every meaningful capability must have a reliable
    verification mechanism.
-   **Deterministic first:** prove deterministic behavior without models
    wherever possible.
-   **Minimal change:** repair the smallest verified root cause.
-   **One active objective:** one primary milestone at a time.
-   **Evidence before promotion:** agent claims are not evidence.
-   **Bounded autonomy:** agents operate only inside explicit scope and
    stop conditions.
-   **No requirement drift:** acceptance criteria cannot be weakened to
    make an implementation pass.

## 3. Development modes {#3-development-modes}

### Exploration

Used for architecture discovery, alternatives, benchmarking and
hypotheses. Every exploration should record findings, options,
trade-offs, decision and consequences.

### Delivery

Used for an approved capability. Delivery stays narrow; architecture is
reopened only when evidence demonstrates that the current design cannot
satisfy the specification.

## 4. Required task loop {#4-required-task-loop}

``` text
Read state
→ Identify gate
→ Inspect
→ Baseline
→ Plan
→ Minimal implementation
→ Targeted test
→ Regression test
→ Diff audit
→ Acceptance check
→ Evidence
→ Promote or stop
```

## 5. Agent preflight {#5-agent-preflight}

Before editing, establish:

``` text
ACTIVE MILESTONE:
TASK:
WHY:
FILES IN SCOPE:
PROTECTED BEHAVIOR:
ACCEPTANCE CRITERIA:
TEST STRATEGY:
REAL-MODEL TEST REQUIRED?:
TOKEN/COMPUTE BUDGET:
STOP CONDITIONS:
EXPECTED EVIDENCE:
```

If a material field cannot be established from project state, stop and
report.

## 6. Roadmap {#6-roadmap}

``` text
M1.0 Foundation
M1.1 Two-model correctness
M1.2 Three-model real validation
M1.3 Three-model repeatability
M1.4 3+ model scaling
M1.5 Model-order invariance
M1.6 Leave-one-out / marginal-value analysis
M1.7 Hardware profiling
M1.8 Universal model-stack recommendation
M2 Conversational Council
M3 Evidence-enabled Council
M4 Adaptive Council
M5 Personalized model stacks
M6 Mature Council
```

## 7. Stage 0 --- Freeze contract {#7-stage-0--freeze-contract}

Create/restore `PROJECT_STATE.md`, lock one active milestone, record
known defects, proven/unproven capabilities and promotion states:

``` text
PASS
CONDITIONAL PASS
FAIL
BLOCKED
NOT PROVEN
```

Exit only when current milestone, acceptance criteria, protected
invariants and known blockers are explicit.

## 8. Stage 1 --- Refactor around contracts {#8-stage-1--refactor-around-contracts}

Isolate, where needed:

-   provider/runtime contract;
-   contribution validation;
-   provenance;
-   disagreement/epistemic semantics;
-   round state;
-   persistence/replay;
-   telemetry.

Public session semantics remain stable unless explicitly changed by
specification.

## 9. Stage 2 --- Repair the state machine {#9-stage-2--repair-the-state-machine}

Mechanically enforce:

``` text
invalid Round 1 → Round 2 blocked
invalid Round 2 → dependent rounds blocked
skipped round → explicit SKIPPED
partial synthesis → incomplete/partial session
provider success ≠ Council completion
```

All early exits must preserve the relevant state.

## 10. Stage 3 --- Rebuild the verification harness {#10-stage-3--rebuild-the-verification-harness}

Treat `self_test.py` as a release-verification subsystem. Separate:

-   literal test status;
-   required/optional policy;
-   infrastructure limitation;
-   promotion status.

A failed optional test may be non-blocking, but it must remain visible
and must not create a contradictory top-line PASS.

## 11. Stage 4 --- Persistence and replay {#11-stage-4--persistence-and-replay}

Prove:

``` text
create → save → load → replay
```

Required state includes question, requested/actual model, role, round,
contribution status, claims, disagreements, uncertainties, synthesis,
terminal state and telemetry relied upon by promotion.

Define `FULL`, `PARTIAL` and `SUMMARY_ONLY` replay explicitly.

## 12. Stage 5 --- Deterministic semantic proof {#12-stage-5--deterministic-semantic-proof}

Test:

-   malformed/empty/partial/truncated output;
-   provenance attacks;
-   identity mismatch;
-   duplicate actual models;
-   claim alignment;
-   disagreement semantics;
-   consensus-not-verification;
-   blocked/skipped/completed state transitions;
-   partial synthesis;
-   terminal-state handling;
-   telemetry structure and persistence.

Promote known bad sessions into regression fixtures where useful.

## 13. Stage 6 --- Bounded real-model verification {#13-stage-6--bounded-real-model-verification}

Create a dedicated audit runner separate from developer convenience
smoke paths.

Rules:

-   compact fixed question;
-   strict token/output cap;
-   strict timeout;
-   explicit model discovery;
-   explicit requested vs actual identity;
-   no hidden escalation;
-   no hidden retries after success;
-   no model download;
-   minimum model count.

Preferred progression:

``` text
deterministic → infrastructure diagnosis if needed → 2-model proof → 3-model proof
```

## 14. Stage 7 --- Promotion {#14-stage-7--promotion}

M1.2 is promotable only when the required deterministic suite, truthful
harness, required runtime evidence, 2-model proof, 3-model proof where
three distinct READY models exist, provenance, state machine,
persistence, replay and required telemetry all pass.

If infrastructure prevents 3-model evidence, status is `BLOCKED` or
`NOT PROVEN`, not PASS.

## 15. Stage 8 --- Three-model repeatability {#15-stage-8--three-model-repeatability}

Use a small controlled matrix:

1.  agreement-oriented question;
2.  disagreement-oriented question;
3.  uncertainty-oriented question.

Measure completion, validity, disagreement, uncertainty, synthesis,
latency, tokens, persistence and replay.

## 16. Stage 9 --- 3+ model scaling {#16-stage-9--3-model-scaling}

Where hardware permits, test 3, 4 and 5 participants. Investigate
comparison scaling, prompt growth, synthesis degradation, telemetry
growth, persistence growth, latency, duplicates and failure isolation.

## 17. Stage 10 --- Hardware/model profiling {#17-stage-10--hardwaremodel-profiling}

Measure warm-up, TTFT, tokens/sec, total latency, RAM, VRAM,
structured-output reliability and failure rate. Then derive model
fitness and model-stack recommendations.

## 18. Change management {#18-change-management}

Every change must answer:

``` text
What defect/requirement does this address?
What must remain unchanged?
How will success be proven?
```

Do not combine unrelated feature work, refactoring and optimization.

## 19. Corrective loop {#19-corrective-loop}

Default maximum: **3 corrective iterations per bounded defect cluster**.

After three unsuccessful iterations:

``` text
STOP
```

Report failure, attempts, evidence, suspected root cause and the minimum
required decision.

## 20. Testing pyramid {#20-testing-pyramid}

``` text
Static inspection
→ Unit
→ Failure injection
→ Fixtures
→ Integration
→ Real-model smoke
→ Real-model promotion
→ Real-user workflow
```

Use the cheapest reliable layer capable of proving the property.

## 21. Real-model budget {#21-real-model-budget}

Before every real-model call:

1.  Can deterministic logic prove it?
2.  Can a fixture prove it?
3.  Can fewer models prove it?
4.  Can less output prove it?
5.  Is repetition itself required?

Only execute the minimum sufficient experiment.

## 22. Definition of done {#22-definition-of-done}

Done means implementation exists, targeted/regression tests pass,
required runtime evidence exists, diff is reviewed, protected behavior
is intact, acceptance criteria have evidence and project state is
updated.

## 23. Rollback {#23-rollback}

When a change regresses behavior:

1.  preserve failure;
2.  identify the first failing invariant;
3.  isolate the smallest affected change;
4.  revert or correct minimally;
5.  rerun targeted tests;
6.  rerun regression tests;
7.  update state.

Do not stack speculative fixes on unstable changes.

## 24. Final principle {#24-final-principle}

> **Optimize for verified product progress, not activity.**
