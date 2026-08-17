# Model Council --- TEST_SPEC.md {#model-council--test_specmd}

> **Role:** Verification and promotion contract. **Purpose:** Define how
> Model Council behavior is proven without relying on model opinion or
> misleading test summaries.

## 1. Verification philosophy {#1-verification-philosophy}

A test proves only the property it actually exercises. Distinguish:

``` text
Executable evidence ≠ model opinion ≠ developer claim ≠ product readiness
```

## 2. Verification layers {#2-verification-layers}

1.  **Static:** source/schema/config inspection.
2.  **Unit:** deterministic parsing, state, provenance and semantic
    rules.
3.  **Failure injection:** timeout, malformed output, truncation,
    provider failure and persistence failure.
4.  **Fixtures:** known-good and known-bad sessions.
5.  **Integration:** provider, persistence and checkpoint behavior.
6.  **Real model:** actual availability, health, warm-up, structured
    output, latency, tokens and multi-model execution.

## 3. Test status contract {#3-test-status-contract}

Every test has:

``` text
PASS
FAIL
SKIPPED
BLOCKED
NOT_RUN
```

and records name, category, required/optional status, duration, evidence
and error.

## 4. Promotion status {#4-promotion-status}

The promotion evaluator uses:

``` text
PASS
CONDITIONAL PASS
FAIL
BLOCKED
NOT PROVEN
```

A failed subtest must never disappear because it is optional. If policy
allows an optional failure, expose it explicitly, e.g.
`test_status=FAIL` and `promotion_status=CONDITIONAL PASS`.

## 5. State-machine tests {#5-state-machine-tests}

Test:

-   valid Round 1 → Round 2 allowed;
-   invalid Round 1 → Round 2 blocked;
-   valid Round 2 → dependent round allowed;
-   invalid Round 2 → dependent round blocked;
-   intentionally unassigned Round 3 → explicit SKIPPED;
-   partial synthesis → incomplete/partial session;
-   provider success + invalid contribution → not completed;
-   early return → relevant round state persisted.

## 6. Contribution tests {#6-contribution-tests}

Test valid structured output, malformed JSON, empty output, truncated
output, syntactically valid but semantically incomplete output, fenced
output, valid JSON followed by prose, provider `done_reason=length`, and
provider errors.

The test oracle must distinguish `valid`, `partial`, `malformed`,
`empty` and `invalid`.

## 7. Provenance tests {#7-provenance-tests}

The application must own identity. Test model-generated attempts to
inject:

-   supporting models;
-   contradicting models;
-   participants;
-   verification authority.

Test requested vs actual identity, duplicate actual models, unknown
models and fabricated participants.

## 8. Role tests {#8-role-tests}

Given:

``` text
A → Analyst
B → Comparator
C → Challenger
D → Synthesizer
```

verify role persistence, correct round association, resistance to model
self-reassignment and explicit handling of failed designated roles.

## 9. Disagreement tests {#9-disagreement-tests}

Minimum invariants:

``` text
support + support → AGREEMENT
oppose + oppose → AGREEMENT
support + oppose → MATERIAL_DISAGREEMENT
unknown + support → UNCERTAINTY
unknown + unknown → UNCERTAINTY
```

Also test wording differences, duplicate claims, claim alignment,
N-model comparison and missing assessments.

## 10. Verification firewall tests {#10-verification-firewall-tests}

Prove that:

``` text
consensus ≠ verification
corroboration ≠ verification
model confidence ≠ factual truth
```

Model-generated `VERIFIED` fields cannot override application-owned
verification status.

## 11. Freshness tests {#11-freshness-tests}

Current-information workflows must distinguish model knowledge from
external evidence. No current verification may be claimed without the
required current evidence path.

## 12. Persistence tests {#12-persistence-tests}

Create a synthetic session with question, participants, roles, rounds,
claims, disagreements, uncertainties, synthesis, terminal status and
telemetry. Then save, load and compare required fields.

## 13. Replay tests {#13-replay-tests}

### FULL

All required deliberation information is available.

### PARTIAL

Some information is unavailable and the limitation is explicit.

### SUMMARY_ONLY

Only summary information exists and the UI/report does not imply full
replay.

## 14. Telemetry lifecycle tests {#14-telemetry-lifecycle-tests}

Prove both synthetic persistence and real execution:

``` text
provider call
→ telemetry generation
→ assignment
→ round
→ session
→ save
→ load
→ replay
```

If telemetry is relied upon by promotion, its persisted presence is
mandatory.

## 15. Harness truthfulness tests {#15-harness-truthfulness-tests}

Deliberately test:

-   required PASS + optional FAIL;
-   required FAIL;
-   infrastructure BLOCKED.

The report must expose failures and policy consequences without
contradictory top-level status.

## 16. Result-shape robustness {#16-result-shape-robustness}

Test all terminal states:

``` text
COMPLETED
PARTIAL
FAILED
BLOCKED
INSUFFICIENT_EVIDENCE
SKIPPED
```

The harness must inspect terminal status before dereferencing optional
fields such as `final_answer`.

## 17. Real-model smoke {#17-real-model-smoke}

A smoke test must use:

-   exact model list;
-   explicit discovery;
-   compact question;
-   strict output cap;
-   strict timeout;
-   no hidden model download;
-   no unnecessary retries;
-   explicit terminal status.

Its purpose is provider/runtime proof, not maximum answer quality.

## 18. Two-model promotion test {#18-two-model-promotion-test}

Use exactly two distinct actual models. Verify discovery, compatibility,
health, warm-up, roles, rounds, provenance, comparison, disagreement,
synthesis, persistence, replay and telemetry.

Record session ID, requested/actual models, roles, round states, claims,
disagreements, telemetry and replay status.

## 19. Three-model promotion test {#19-three-model-promotion-test}

Use exactly three distinct READY actual models. Fewer than three actual
participants or duplicate identity invalidates the test.

Verify N-way comparison, provenance, role integrity, disagreement,
uncertainty, round gating, synthesis, persistence, replay and telemetry.

## 20. Three-model repeatability {#20-three-model-repeatability}

Only when repeatability is an explicit acceptance criterion. Use three
controlled questions:

-   agreement;
-   disagreement;
-   uncertainty.

Record completion, validity, disagreement, uncertainty, synthesis,
latency, tokens, persistence and replay.

## 21. 3+ scaling {#21-3-scaling}

Where hardware permits, test 3, 4 and 5 models. Investigate comparison
scaling, prompt growth, synthesis size, latency, token growth, session
size, telemetry growth, failure isolation and duplicates.

## 22. Model-order invariance {#22-model-order-invariance}

Change model order and verify that provenance and role semantics do not
change merely because seat ordering changed.

## 23. Leave-one-out / marginal value {#23-leave-one-out--marginal-value}

Compare:

``` text
A+B+C
A+B
A+C
B+C
```

Measure unique claims, new/resolved disagreements, uncertainty
reduction, evidence targets, quality, latency and token cost.

## 24. Hardware/model health tests {#24-hardwaremodel-health-tests}

Test:

``` text
discover → profile → compatibility → health → warm-up → ready
```

Include compatible, incompatible, cold-start, warm-start, timeout and
structured-output failure cases where safely reproducible.

## 25. Performance tests {#25-performance-tests}

Measure warm-up, TTFT, tokens/sec, total model latency, round latency,
Council latency, tokens, persistence latency and session size.

Do not optimize before measuring.

## 26. Golden sessions {#26-golden-sessions}

Maintain fixtures for:

-   complete good session;
-   disagreement session;
-   uncertainty session;
-   provenance attack;
-   truncation;
-   blocked session;
-   partial session.

## 27. Real-model budget {#27-real-model-budget}

Default promotion evidence is:

``` text
one bounded 2-model run
+
one bounded 3-model run
```

Do not repeat unless repeatability is the target property.

## 28. Evidence requirements {#28-evidence-requirements}

Promotion claims should record:

-   test name;
-   command;
-   result;
-   timestamp when useful;
-   real session ID;
-   actual model identities;
-   artifact/evidence location.

Use labels:

``` text
VERIFIED
OBSERVED
NOT TESTED
BLOCKED
NOT PROVEN
```

## 29. Stop conditions {#29-stop-conditions}

Stop when acceptance criteria pass, required infrastructure blocks
progress, test validity is compromised, three corrective iterations
fail, scope expands, or destructive action is required.

## 30. Promotion gate {#30-promotion-gate}

Promote only when required deterministic tests, truthful harness
reporting, required integration evidence, required real-model evidence,
provenance, state machine, persistence, replay, required telemetry and
regression checks all pass.

Otherwise use `FAIL`, `BLOCKED` or `NOT PROVEN` as appropriate.

> **Verification principle:** Use the cheapest reliable test capable of
> proving the property, and never substitute model agreement for
> executable evidence.
