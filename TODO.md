# Kent — TODO

Near-term work. Status of every control: [`docs/controls.md`](docs/controls.md);
assessment history and open items: [`docs/conformance.md`](docs/conformance.md).

**Status:** v0.1.0 release candidate

## To v0.1.0

- [ ] Anthropic key: `--config prod`, `FALLBACK_TIMEOUT=120`, re-run conformance and one tiny Gent simulation with live Opus
- [ ] Merge the release branch and tag `v0.1.0`

## To v0.2.0 — traceability and determinism (design pending)

- [ ] End-to-end trace correlation: one W3C `traceparent` from Kent through the Gent CEO and workers
      to the gateway and tools; OpenTelemetry GenAI spans (`invoke_agent`, `chat`, `execute_tool`,
      semconv version pinned — still "Development" upstream); local only (Alloy as collector, Grafana
      Tempo beside Loki/Prometheus); LiteLLM OTel (semconv opt-in, OTel v2)
- [ ] Run manifest: one record per Gent run binding model hash, prompt/template/SOUL hashes, sampling
      settings and seed, tool and image versions, gateway config version; hashed into the audit chain
- [ ] Record / replay through the gateway: record a run's model responses, replay them so a Gent run
      (e.g. the capability check) is a deterministic regression test and a failure can be reproduced
      step by step; note llama.cpp's nondeterminism across batch sizes with 2 slots

## Regulated-workload gaps (controls.md)

- [ ] DP-3 Redaction / DLP before cloud calls; DP-4 provider zero-retention + BAA/DPA
- [ ] DP-5 Encryption at rest; DP-6 backups; RO-4 disaster-recovery plan
- [ ] LM-6/7 Off-host immutable log shipping with configurable retention
- [ ] AC-7 MFA/SSO for Grafana and Gitea
- [ ] NS-4 Host firewall default-deny; NS-5 egress POST allowlist with approvals
- [ ] CM-6 Vulnerability scanning + SBOM (pip-audit, trivy); CM-7 AIDE; CM-8 scheduled key rotation
- [ ] RO-6 systemd-oomd policies for heavy services

## Features

- [ ] CPU router bake-off (shadow mode): replace the LLM classifier on the llama slot with a CPU
      classifier, chosen by an A/B "beauty pageant" on Kent's own traffic
      - Candidates: A = ModernBERT-large-llm-router (binary, calibrated, 2 thresholds → 3 tiers);
        B = NVIDIA prompt-task-and-complexity-classifier (task type + 6 dimensions; license check);
        both INT8 ONNX on CPU, pinned hashes, hash-locked `onnxruntime`
      - Shadow: a read-only LiteLLM *routing* plugin (`complexity_router_config.plugins`, 1.100.1) —
        runs after the live LLM classifier, sees the messages and the chosen tier's pool, leaves the
        candidates untouched, catches every error, and scores A and B off the request path
        (background task) so `auto` latency is unchanged; both verdicts logged beside the live one
      - Go-live: the winner becomes a *classifier* plugin (`classifier_type: custom`)
      - Labelled set: sampled `auto` prompts (needs content capture), labels from the LLM router,
        operator corrections and outcomes (failed local answers = should have gone up)
      - Score: agreement with labels, under-routing weighted above over-routing, consistency
        (same/similar prompt → same tier), p50/p95 latency, CPU and llama decode slowdown while
        running
      - Switch count: tier changes per agent loop where the input changed only by an appended tool
        result (noise, not a change in the work); downward switches counted separately. Switches at
        task/sub-task boundaries or Gent escalations are legitimate and not counted. Each noise
        switch re-sends the conversation uncached (cloud prompt cache, llama KV cache)
      - Winner goes live biased upward (unsure → one tier up; `fallback_tier: frontier` for failures);
        later: fine-tune a small encoder on Kent's labels
- [ ] Egress guard (shadow mode first): scan every cloud-bound request (smart/frontier, including
      `auto` resolved to a cloud tier) for secrets and PII; local-bound traffic is not scanned
      - **Moved (2026-10-02):** built as a separate package, Prompt Privacy Enhancement
        (`~/Documents/repo/prompt-privacy-enhancement`, parked; see its docs/design/RESUME.md).
        Kent's part is only the wiring (installer, zones, credentials, conformance, bench). The
        notes below are the original Kent-side sketch
      - Secrets: LiteLLM's built-in `litellm_content_filter` (regex, no new services; keys, tokens,
        cards, IBAN). Its MASK is one-way (redacted, not restored): right for secrets only
      - PII: Presidio Analyzer container (NER), swap-and-restore: identifiers leave as placeholder
        tokens, the reply (incl. streamed text and tool-call arguments) gets the real values back
        at the gateway. LiteLLM's `presidio` hook does this (`output_parse_pii`), but its tokens
        are `<PERSON_1>` numbered by position, so two values can share a token within one
        conversation and restore the wrong one (unverified), and they are useless for audit
      - Own tokens instead (implemented in `kent_gateway.py`, calling the Analyzer over HTTP):
        `<PERSON_7f3a9c2e1b>` = entity type + truncated HMAC(key, conversation id | type |
        normalised value). Same value → same token for the whole conversation (no collisions; the
        history stays byte-identical, so cloud prompt caching still hits); different conversations
        → different tokens (the provider cannot link them). Short and opaque so the model copies
        it faithfully; no host, container or PID inside (internal metadata stays home)
      - Audit record per masked value (Kent's audit chain or stack.db; never Loki, never the
        plaintext): token, value digest = HMAC(key, type | normalised value) (conversation-free),
        entity type, caller identity (kent / gent-<id> from the virtual key), conversation/trace
        id, request id, tier/model, timestamp. Forensics: "did passport K1234567 ever leave?" →
        compute its digest, search; "what was <PERSON_7f3a…> in this provider log?" → look up
        the token. The plaintext mapping lives in memory only until the reply is restored
      - Needs a conversation id from the caller (header; ties into v0.2.0 trace correlation);
        fallback: request id (tokens then stable per call only)
      - Entity policy: mask identifiers (name, passport/NRIC/FIN, street address, phone, email);
        keep attributes the question may need (nationality, country, city). Custom recognisers
        for SG passport numbers and SG street addresses (Presidio's defaults miss them)
      - Placement: pre-call guardrails may run before `auto` resolves its tier (unverified in
        1.100.1); doing it in `kent_gateway.py` once the tier is known avoids that
      - Action: log-only, then mask (PII) / block (secrets) / cap at local (hit → `fast` instead
        of cloud) per entity class
      - Cost: cache Analyzer results per message hash so each agent step scans only the new message
      - Injection: content-filter `prompt_injection_*` categories (or a classifier) as a logged
        signal only, never the defence; measure on the bench's planted-injection items (b24)
      - Covers the audit's PII-detection item (2026-09-28 review: CSA AI guidance Addendum 4.1/4.2)
- [ ] Seed-from archive (architecture §11.2)
- [ ] Push adopted learnings to running Gents; give the judge its past verdicts (dedupe)
- [ ] Stronger learning uptake by local models (make "apply team knowledge" part of expected output)
- [ ] Dev-mode judge: raise the adoption bar or require operator approval when the judge is local
- [ ] llama.cpp as a managed service (own account and unit)
- [ ] Grafana alert rules + notification channel
- [ ] Layer-3 prompt-injection tests against Kent (sandboxed profile)

*Items move to GitHub Issues when concrete enough to assign. Last updated: 2026-10-01.*
