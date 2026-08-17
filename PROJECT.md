# Model Council --- PROJECT.md {#model-council--projectmd}

> **Role:** Authoritative product and runtime contract. **Purpose:**
> Define what Model Council is, how it must behave, and the
> non-negotiable product/runtime invariants.

## 1. Vision {#1-vision}

Model Council is a hardware-aware, provider-agnostic multi-model
deliberation system. It is designed to obtain independent perspectives,
compare claims, expose agreement/disagreement and uncertainty, use
current evidence when required, and produce a traceable synthesis.

It is not merely "ask several models and combine their answers." The
user must be able to inspect the deliberation, understand model roles,
see the arguments and disagreements, and replay the session.

## 2. Product priorities {#2-product-priorities}

1.  Correctness and safety.
2.  Hardware/model compatibility and truthful readiness.
3.  Provenance and role integrity.
4.  Deliberation and epistemic integrity.
5.  Evidence freshness when current information matters.
6.  Reliability and answer quality.
7.  Full session transparency and replay.
8.  Latency efficiency.
9.  Token efficiency.
10. Compute efficiency.

Performance optimization is subordinate to correctness, but unnecessary
latency/tokens/compute must not be accepted without measurement or
justification.

## 3. Runtime architecture {#3-runtime-architecture}

``` text
System Profile
  → Model Discovery
  → Hardware Compatibility
  → Model Health
  → Model Fitness
  → Model-Count Recommendation
  → Selection
  → Warm-up
  → Round 1 Analysis
  → Validation Gate
  → Round 2 Comparison
  → Debate Validation
  → Round 3 Challenge
  → Round 4 Synthesis
  → Evidence/Freshness Check
  → Final Result
  → Persistence
  → Replay
```

The conceptual layers are:

-   **System/provider runtime:** hardware, Ollama/provider discovery,
    health, warm-up, execution, latency and token telemetry.
-   **Council engine:** selection, roles, rounds, gating, checkpoints
    and synthesis orchestration.
-   **Epistemic engine:** claims, positions, disagreement, uncertainty,
    corroboration and verification status.
-   **Evidence engine:** current-information retrieval and
    claim-to-source mapping.
-   **Adaptive computation:** minimum sufficient model count and
    targeted escalation.
-   **Hardware/model intelligence:** compatibility, empirical
    performance and model-stack recommendation.
-   **User experience:** full session history, round inspection, replay
    and resume.

## 4. System profile {#4-system-profile}

A local model must not be considered usable merely because it is
installed. Before normal participation, the Council should inspect,
where available:

-   CPU
-   system RAM and available RAM
-   GPU and vendor
-   VRAM and available VRAM
-   operating system
-   Ollama/runtime availability
-   model size and quantization
-   configured context window
-   expected resource requirements

The system profile should be persisted when it is used to make a
compatibility or model-selection decision.

## 5. Model lifecycle {#5-model-lifecycle}

These states are distinct:

``` text
DISCOVERED
INSTALLED
COMPATIBLE
CONDITIONALLY_COMPATIBLE
INCOMPATIBLE
HEALTHY
UNHEALTHY
READY
TIMEOUT
UNAVAILABLE
FAILED
```

Never infer:

``` text
Installed ≠ Compatible
Compatible ≠ Healthy
Healthy ≠ Ready
Ready ≠ Suitable for every task
```

If a model is incompatible, warn the user before normal execution.
Explain possible memory pressure, swapping, excessive latency or runtime
failure. Do not silently treat it as a valid participant.

## 6. Model health {#6-model-health}

A health check must test actual runtime behavior. It should establish,
where applicable:

-   model can load;
-   provider can execute it;
-   usable output is returned;
-   required structured output can be produced;
-   actual model identity matches the requested model;
-   basic latency is measurable;
-   immediate resource/runtime failure does not occur.

"Discovered" or "installed" is never equivalent to "healthy."

## 7. Model fitness and model-count recommendation {#7-model-fitness-and-model-count-recommendation}

A healthy model is not automatically suitable for the current task.
Fitness should consider:

-   hardware compatibility;
-   task requirements;
-   structured-output reliability;
-   reasoning capability;
-   context requirements;
-   model diversity;
-   latency;
-   token/compute cost;
-   historical/runtime reliability.

Before execution, the Council should recommend the minimum sufficient
number of models. Inputs include task complexity, ambiguity, reliability
needs, need for independent views/challenge, evidence requirements,
compatible model inventory, latency budget, token budget and hardware
capacity.

Additional models require a justification based on expected marginal
information or risk reduction.

## 8. Warm-up {#8-warm-up}

Selected models should be warmed before the first substantive inference
whenever technically feasible.

``` text
Profile → Compatibility → Health → Selection → Warm-up → READY → First inference
```

Warm only selected models. Record warm-up start/end and result when
possible. A warm-up failure must be surfaced; silent substitution is
prohibited unless an explicit fallback policy exists.

## 9. Role integrity {#9-role-integrity}

The application owns model identity, role, seat, round and provenance.

If the role plan says:

``` text
A → Analyst
B → Comparator
C → Challenger
D → Synthesizer
```

models cannot redefine their own roles. A failed designated model must
be marked failed/blocked or handled through an explicit fallback policy.
Silent role substitution is forbidden.

## 10. Round contract {#10-round-contract}

### Round 1 --- Analysis {#round-1--analysis}

Expected information includes understanding, position, claims,
assumptions and uncertainties.

### Round 2 --- Comparison {#round-2--comparison}

Expected information includes claim-level assessments, agreements,
disagreements, arguments and uncertainty/evidence gaps.

### Round 3 --- Challenge {#round-3--challenge}

Expected information includes challenged claims, counterarguments,
weaknesses, alternative interpretations and remaining uncertainty.

### Round 4 --- Synthesis {#round-4--synthesis}

Expected information includes inputs considered, agreements, unresolved
disagreements, evidence status, recommendation and rationale.

The exact schema may evolve, but application-owned semantic
responsibilities must remain explicit.

## 11. Debate integrity {#11-debate-integrity}

Multiple model outputs do not automatically constitute debate.
Comparison/challenge rounds must contain enough structured evidence to
establish that the assigned deliberation actually occurred.

If a required comparison/debate response cannot provide the required
structure, the process must be classified as failed, blocked or
insufficient-evidence according to the state machine. The application
must never fabricate disagreements.

## 12. Disagreement semantics {#12-disagreement-semantics}

Application-owned deterministic semantics are authoritative:

``` text
support + support = AGREEMENT
oppose + oppose = AGREEMENT
support + oppose = MATERIAL_DISAGREEMENT
unknown / uncertain = UNCERTAINTY / INSUFFICIENT_EVIDENCE
```

Differences in wording alone must not manufacture disagreement.

## 13. Verification firewall {#13-verification-firewall}

Keep these concepts separate:

``` text
Model confidence ≠ Claim confidence
Consensus ≠ Corroboration
Corroboration ≠ Verification
Pretrained knowledge ≠ Current information
```

Models cannot declare their own outputs verified. Verification status is
application-owned.

## 14. Freshness and evidence {#14-freshness-and-evidence}

For changing information such as current software versions, prices,
regulations, market information, events, availability or specifications,
pretrained model knowledge alone is insufficient when freshness matters.

Preferred flow:

``` text
Claim → Evidence required? → Retrieval → Source → Validation → Claim/source mapping → Evidence status
```

If current evidence cannot be obtained, state that limitation. Never
imply current verification from model consensus alone.

## 15. Full session transparency {#15-full-session-transparency}

The user must be able to inspect the complete session rather than only a
summary. A replayable session should expose, subject to privacy policy:

-   question;
-   requested and actual models;
-   roles and seats;
-   health/warm-up state;
-   round status;
-   model outputs;
-   claims, assumptions and uncertainties;
-   comparisons and disagreements;
-   challenges;
-   evidence;
-   synthesis;
-   telemetry;
-   errors and blocked/skipped reasons.

## 16. Persistence and replay {#16-persistence-and-replay}

Persist enough state to reconstruct the deliberation. At minimum
preserve question, participant provenance, round assignments,
contribution statuses, claims, disagreements, uncertainties, synthesis,
terminal state and telemetry relied upon by audit/promotion.

Replay modes must be explicit:

``` text
FULL
PARTIAL
SUMMARY_ONLY
```

The system must not label a partial replay as full.

## 17. Telemetry {#17-telemetry}

Telemetry is observational and must not alter Council semantics. Track
where available:

-   request start;
-   response completion;
-   latency;
-   input/output tokens;
-   done reason;
-   warm-up duration;
-   total session latency;
-   persistence latency;
-   session size.

If telemetry is used by an audit or promotion gate, it must survive
persistence and reload.

## 18. Token and latency efficiency {#18-token-and-latency-efficiency}

Before each model call ask:

1.  Can deterministic logic answer this?
2.  Can existing evidence answer this?
3.  Can fewer models answer this?
4.  Can less context answer this?
5.  Can a smaller output satisfy the schema?

Use the minimum sufficient computation. Do not reduce output so
aggressively that claims, disagreement, uncertainty, evidence or
synthesis quality are lost.

## 19. Failure semantics {#19-failure-semantics}

Use explicit terminal/round states such as:

``` text
COMPLETED
PARTIAL
FAILED
BLOCKED
SKIPPED
INSUFFICIENT_EVIDENCE
```

Provider success does not imply Council completion. Optional fields such
as `final_answer` must be interpreted only after terminal state is
known.

## 20. Non-negotiable constraints {#20-non-negotiable-constraints}

The Council must not:

-   invent model availability;
-   invent hardware capability;
-   invent evidence or test results;
-   weaken tests to obtain PASS;
-   silently change roles;
-   silently substitute models;
-   silently promote blocked/partial execution;
-   expose credentials;
-   download models without authorization;
-   make unrelated architecture changes.

## 21. Adaptive Council direction {#21-adaptive-council-direction}

Long-term behavior:

``` text
Task
  → minimum model estimate
  → initial deliberation
  → disagreement/uncertainty/evidence assessment
  → decide whether more computation is justified
  → targeted escalation or synthesis
```

Additional computation must have measurable or explicit justification.

## 22. Product completion {#22-product-completion}

A capability is complete only when implementation, tests, runtime
evidence where required, persistence, protected invariants and
acceptance criteria are all supported by evidence.

> **Product commandment:** Use the minimum computation required to
> produce the maximum trustworthy result while keeping the deliberation
> inspectable and epistemically honest.
