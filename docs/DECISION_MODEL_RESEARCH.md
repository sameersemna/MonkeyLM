# System-One research

Note: The §3 thresholds are my own proposed starting values, derived by arithmetic from the cited numbers. They are not published standards.

## 1. Top 5 evidence-backed use cases (ranked by evidence × fit)

1. **Loop-escape reranking over ≤8 finite candidates (shadow, then loop-only).**
   - At 50 nearest-neighbour tools, Jev holds about 98% (on items with a unique correct tool) vs Laya's 31% (sys1-eval abstract).
   - SystemOneHarness compiles finite typed action spaces.
   - The evidence is for Jev and Laya, not V1M. *Medium.*
2. **Risk-tiered gates (read/write/destructive) on chosen-option probability plus margin.**
   - SystemOneHarness README tiers are 0.5/0.6/0.8.
   - TypeSafe docs say `confidence` is not P(correct).
   - The tier values are unvalidated README examples. *Medium.*
3. **Multi-signal escalation (loop/stuck/blocked/novelty plus confidence) to the main LLM or a re-plan.**
   - System Switch: deferring the least-confident 30% beats random deferral (+0.13 native order, +0.08 shuffled).
   - No variant reached the exit in 33 closed-loop games.
   - It mirrors MonkeyLM's existing `loop_triggered`. *Medium.*
4. **Untrusted-DOM injection defense-in-depth.** Use provenance delimiters, with a guard model only as an optional pre-filter.
   - INFUSE: typed provenance delimiters cut attack success by 32.5 points, at a 3.6–18.3 point utility cost (preprint).
   - OWASP 2026: "no reliable prevention mechanism exists today."
   - *Medium–High for delimiters; guards unproven.*
5. **Defect-claim triage (automation artifact vs real defect).**
   - The need is well evidenced. WebProber found 85% of reported bugs were false positives. WebTestBench found every model scored F1 below 30%.
   - No independent evidence shows a System-1 model fixes this, so run it as an experiment only. *Low (fit).*

## 2. Prioritized decisions (as in §5.1 / 5.2 / 5.3 / 5.5)

**5.1 Implement**
- **P0**
  1. Bounded retry: 2–4 attempts and full-jitter exponential backoff.
     - Honor `Retry-After` and `x-should-retry`.
     - Do not retry spend/quota 429s.
     - Use one shared backoff for concurrent failures, plus a circuit breaker.
  2. Pin the exact model ID (not `rev-latest`), log the response `model`, and recalibrate on change.
  3. Make chosen-option probability plus margin the primary gate. Keep raw `confidence` log-only.
  4. Use per-risk-class thresholds instead of one global 0.75.
- **P1**
  5. Run a candidate-order permutation test.
  6. Verify per-provider state and question limits. Don't assume 64,000 chars is safe.
  7. Build a labeled replay set with hundreds of positives per risk tier.
  8. Keep the multi-signal loop gate dominant and confidence supplementary.
- **P2**
  9. Run a same-replay A/B of V1M vs Jev vs Laya vs no-model.
  10. Treat provenance delimiters as the primary injection defense, and any guard model as a pre-filter only.
  11. Separate automation artifacts from site defects.
  12. Add trajectory-level metrics.

**5.2 Reject**
1. Zero-shot System-1 routing between LLMs.
2. System-1 as the sole RAG-skip or "don't re-observe" gate.
3. System-1 as the sole safety/PII gate.
4. Raw `confidence` as P(correct).
5. V1M for form-payload generation or security verdicts.
6. Trusting vendor latency or "zero false alert" claims.
7. SecAlign/StruQ-style trained defenses, which don't apply to hosted APIs.
8. V1M or any System-1 model as a hard dependency.
9. An LLM defect judge without a second confidence check.

**5.3 Validation experiments**
1. Candidate-order permutation test.
2. Held-out threshold calibration: reliability diagram, ECE, Brier, AUROC, selective accuracy, and miss rate at a fixed call rate.
3. V1M/Jev/Laya bake-off on identical replays.
4. 429/503/529 failure injection, with and without `Retry-After`.
5. Trajectory outcome, rerank off vs on.
6. Adaptive DOM prompt-injection red-team.

**5.5 Rollout**
1. **Instrument and harden (shadow only).** Exit when there are several hundred to 1,000 fully logged shadow decisions, zero provider-error crashes, and retry storms near zero.
2. **Offline calibration plus controlled A/B (shadow plus loop-only rerank).** Exit when a reliability diagram and held-out FP/FN at a fixed call rate exist for at least one provider, and the flip rate is measured.
3. **Gated, capped production rollout with a kill switch.** Roll back automatically if live metrics regress vs Phase 2. Re-calibrate fully on any response-`model` change before enabling by default.

## 3. Three key A/B metrics and thresholds (proposed)

| Metric | Pass threshold | Basis |
|---|---|---|
| **M1: Loop-escape rate** on loop-triggered episodes, rerank on vs off, matched seeds (plus overall new-state discovery) | At least +10 pp absolute, with the 95% CI lower bound above 0. New-state discovery no worse than −2 pp. About 385 episodes per arm detects 50%→60% at 80% power, α=0.05 (recompute from the Phase 1 baseline). | Step accuracy doesn't predict loop success (System1-Arena, System Switch) |
| **M2: Held-out gate error at a fixed call rate.** FP = an applied rerank followed by a revisit or failed plan; measured per risk tier | Held-out error ≤ calibration target + 2.5 pp (a 5% target means ≤7.5%). Use ≥300 labeled positives per tier (95% CI about ±2.5 pp at p=0.05). Always report the call rate. | sys1-eval: a 5% in-sample target gave up to 17% held-out misses |
| **M3: Candidate-order flip rate** (reverse plus 2 shuffles, ≥200 decisions, about ±3 pp at 5%) | ≤5% passes. 5–15% requires permutation voting (cost ×n). Above 15% stays shadow-only. | Laya flips about 30% vs Jev about 1.8% (sys1-eval); my thresholds sit between them |

**Starting gate values to calibrate (not validated):**
- Chosen-option probability of at least 0.5/0.6/0.8 for read/write/destructive actions, from the SystemOneHarness README.
- Keep the existing 0.15 margin.

**Calculation:** Under TypeSafe's Choice formula `(p_max−1/n)/(1−1/n)`, MonkeyLM's current `confidence ≥ 0.75` equals top-probability about 0.79 at 6 candidates (0.78 at 8, 0.875 at 2). That is roughly the "destructive" bar applied to every action. At n=6, the 0.5/0.6/0.8 tiers correspond to `confidence` of about 0.40/0.52/0.76. This is a hypothesis only, because it assumes V1M's confidence follows Jev's formula, which is unverified.

## 4. Biggest caveats and gaps

- **V1M has no independent validation.** It is absent from sys1-eval, System Switch, S1MB and System1-Arena. Its docs are inconsistent, and its `Retry-After` behavior and confidence formula are unknown.
- **The closed-loop evidence is directional.** The System1-Arena abstract confirms "small System-1 models rarely succeed" in closed loops. The specific 67.8% step accuracy and 26.5/17.4/5.7% loop success figures come from a practitioner blog, not the paper.
- **Counterfactual labels are missing.** Shadow mode only logs what V1M would choose. Its benefit can't be measured without randomized exposure or off-policy evaluation, and there is no labeled replay set yet.
- **Most sources are not peer-reviewed.** sys1-eval, System Switch, System1-Arena, BrowseSafe, INFUSE, WebProber and WebTestBench are preprints, and Baboon is a thesis. The peer-reviewed order-bias work covers LLM multiple-choice questions, not encoder System-One models.
- **Unverified or unread:**
  - The SystemOneHarness design doc and source.
  - NeMo Guardrails and Guardrails AI docs.
  - OpenAI Decisions API, Microsoft-Decision-1 and Decision 2.0, which I saw only via one paper.
  - unsloth's client-side 429/503/529 snippet. A follow-up search for it was declined by the session approval system, and I did not retry it.
- **All §3 thresholds are my proposals.** No published standard exists for any of them.

## Corrections report

- **OWASP citation:** The 2026 text is at https://genai.owasp.org/download/56857/. It has 11 mitigations and "no reliable prevention." It also relays Nasr et al. 2025 on adaptive attacks exceeding 90% success; I did not read the primary paper. https://genai.owasp.org/llmrisk/llm01-prompt-injection/ is the 2025 entry (7 mitigations), and I mis-cited it.
- **sys1-eval details:**
  - Jev's "+10.8 to +46.0 pp on 9/11 decision points" is versus Laya, not versus chance. Neither beats chance on routing.
  - At τ=0.1 the Jev pre-screen still sent 65.6% of traffic to the reviewer, had an 11.6% miss rate, and cut cost only 4.3%. My earlier wording was garbled.
  - Laya's roughly 30% flip rate is verified in the abstract. Jev's 1.8% comes from the paper body I read earlier and was not re-fetched.
- **Missing or loose citations:**
  - RouteLLM's URL was not recorded.
  - The Goyal pattern library has no URL.
  - The Nature Reviews Psychology dual-process survey is at https://www.nature.com/articles/s44159-025-00506-1, and I paraphrased its title.
  - CRAG and RouterBench should be treated as preprints, since I did not verify a venue.

## 5. Strongest sources per section

**§1 Use cases**
- 1–2: https://arxiv.org/abs/2610.02267 [preprint] · https://github.com/HarnessRouter/SystemOneHarness [code] · https://docs.typesafe.ai/ [vendor docs]
- 3: https://arxiv.org/abs/2610.09683 [preprint]
- 4: https://genai.owasp.org/download/56857/ [standard] · https://arxiv.org/abs/2610.20776 [preprint] · https://arxiv.org/abs/2511.20597 [preprint] · https://proceedings.nips.cc/paper_files/paper/2025/file/1c9818387f5dd0a0bc151214660f059d-Paper-Datasets_and_Benchmarks_Track.pdf [peer-reviewed]
- 5: https://arxiv.org/abs/2509.05197 [preprint] · https://arxiv.org/abs/2603.25226 [preprint] · https://repositorio-aberto.up.pt/bitstream/10216/176207/2/787502.pdf [thesis]

**§2 Decisions**
- **5.1 (retry):** https://developers.openai.com/api/docs/guides/error-codes · https://platform.claude.com/docs/en/api/errors · https://developer.atlassian.com/cloud/jira/platform/build-an-app-that-respects-rate-limits/ · https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/ [all vendor docs or blogs]
- **5.1 (pin and gate):** https://docs.typesafe.ai/ [vendor docs]
- **5.1 (order bias):** https://proceedings.iclr.cc/paper_files/paper/2024/file/54dd9e0cff6d9214e20d97eb2a3bae49-Paper-Conference.pdf [peer-reviewed]
- **5.2:**
  - https://arxiv.org/abs/2610.02267 [preprint; routing, RAG and safety gates]
  - https://www.langchain.com/blog/building-a-harness-with-jev [vendor-adjacent; its routing claim is contradicted by sys1-eval]
  - https://laya.studio/docs/guard [vendor docs; self-caveating]
  - https://v1m.ir/ [vendor marketing]
- **5.3:** https://aclanthology.org/2024.findings-naacl.130/ [peer-reviewed] · https://arxiv.org/abs/2610.02267 · https://arxiv.org/abs/2511.20597 · https://genai.owasp.org/download/56857/
- **5.5:** https://arxiv.org/abs/2610.21244 [preprint] · https://arxiv.org/abs/2610.09683 [preprint] · https://docs.typesafe.ai/ [alias and version guidance]

**§3 Metrics and thresholds**
- https://arxiv.org/abs/2610.21244
- https://arxiv.org/abs/2610.09683
- https://arxiv.org/abs/2610.02267
- https://github.com/David-DL-Space/sys1-eval [code and data]
- https://github.com/HarnessRouter/SystemOneHarness
- https://docs.typesafe.ai/

**§4 Caveats**
- https://v1m.ir/ [vendor]
- https://www.hsoni.dev/blog/system-one-decision-models [practitioner blog; relays the System1-Arena numbers]
- https://huggingface.co/blog/hotchpotch/system-one-mosaic-benchmark [practitioner; the author has a competing model]
- https://arxiv.org/abs/2610.02267 [preprint; self-audit]