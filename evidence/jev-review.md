# Jev review — authoring-time auxiliary gate (evidence and interpretation)

Model: `jev-1.13.0`. Endpoint: `https://api.typesafe.ai/v1/systemone` (Bearer
from env `TYPESAFE_API_KEY`). Timing: run BEFORE each phase commit on the
non-empty diff of the changed files. This document exists because the final
report must separate model observations from test results and because the
closure review asked for exact field semantics, both question versions after
sharpening, and the data classes that were sent.

## 1. What this is (and is not)

Jev was a **second-opinion advisory gate during authoring**. It never ran as
part of the dashboard runtime, never replaced unit/browser/integration/auth
tests, and never turned a FAIL into a PASS. Acceptance rests on the recorded
suites and logs listed in `TEST_REPORT.json` and `FIX_REPORT.md`.

The dashboard runtime itself makes no network calls (see PUB-T06 and PUB-T04
evidence); the calls below were made by the developer session while preparing
commits.

## 2. Field semantics (per TypeSafe documentation)

- `noul` — the probability that the model answers **"yes"** to the exact
  question asked. A `noul` payload does **not** return a separate
  `confidence` field.
- `confidence` (Choice/Score) — a separate statistic describing the answer
  distribution. It is **not** "probability the program is correct" and is not
  comparable to test pass rates.

Consequently every number in this file is a model observation about the
changed lines under the exact wording quoted, nothing more. The one rule used
operationally: `touches_local_path` had to be LOW before committing, because
the repair must not change local OpenCode accounting.

## 3. Data classes sent to the external API

- `state` = `git diff -- <changed files of the phase>`, UTF-8 with
  `errors="replace"`, truncated to the first 24 000 characters when longer.
- Content: local source, test code and synthetic fixtures of this repository
  only. No tokens, no API keys, no passwords, no user prompts, no session
  data, no database contents.

| Phase | diff chars | sent |
|---|---|---|
| F5a/F6a | 9 071 | full |
| F5b/F5c | 25 314 | truncated to 24 000 |
| F3 | 22 317 | full |
| F7 | 19 455 | full |
| F6b | 12 468 | full |
| F6c | 38 762 | truncated to 24 000 |
| F6d | 70 377 | truncated to 24 000 |
| F6e | 26 253 | truncated to 24 000 |
| F6f | 15 407 | full |

## 4. Recorded results

All values are `noul` for the quoted question. Contexts (file/lines) are in
`FIX_REPORT.md` phase sections.

| Phase | Question | Result |
|---|---|---|
| F5a/F6a | touches_local_path | 0.09 |
| F5a/F6a | touches_router_path | 0.96 |
| F5a/F6a | silent_failure_removed | 0.84 |
| F5a/F6a | atomic_publish_present | 0.96 |
| F5b/F5c | touches_local_path | 0.07 |
| F5b/F5c | touches_codex_reader_path | 0.99 |
| F5b/F5c | shared_enumeration_present | 0.97 |
| F5b/F5c | diagnostics_states_distinguished | 0.97 |
| F5b/F5c | rec_guard_present | 0.98 |
| F3 | touches_local_path | 0.12 |
| F3 | activity_state_separation | 0.99 |
| F3 | boundary_contract_exact | 0.96 |
| F3 | ui_language_updated | 0.96 |
| F3 | limit_counters_present | 0.96 |
| F7 | touches_local_path (round 1 / 2 / 3) | 0.44 / 0.53 / 0.19 |
| F7 | commit_window_meta | 0.97 |
| F7 | boundaries_and_tz | 0.96 |
| F7 | ui_and_export_warnings | 0.93 |
| F7 | no_inferred_attribution | 0.96 |
| F6b | touches_local_path (round 1 / 2) | 0.35 / 0.21 |
| F6b | bounded_tail_no_readlines | 0.96 |
| F6b | count_cache_honest_metadata | 0.94 |
| F6b | oversize_and_pending_reported | 0.92 |
| F6c | touches_local_path | 0.19 |
| F6c | incremental_offsets_correct | 0.78 |
| F6c | fingerprint_not_additive | 0.97 |
| F6c | checkpoint_never_truth | 0.95 |
| F6d | touches_local_path | 0.15 |
| F6d | per_section_independence | 0.95 |
| F6d | deadline_covers_body | 0.92 |
| F6d | no_fake_success | 0.94 |
| F6d | single_build_bounded | 0.84 |
| F6e | touches_local_path | 0.09 |
| F6e | bounded_paging_cursor | 0.97 |
| F6e | batched_parts_not_per_message | 0.94 |
| F6e | response_budget_explicit | 0.88 |
| F6e | controlled_bad_inputs | 0.92 |
| F6f | touches_local_path (round 1 / 2) | 0.13 / 0.14 |
| F6f | per_worktree_ttl_cache_shared | 0.96 |
| F6f | bounded_subprocess_per_refresh (round 1 / 2) | 0.49 / 0.87 |
| F6f | timeout_safe_stale_fallback | 0.92 |
| F6f | argv_list_no_shell | 0.97 |
| F6f | no_disk_scan_no_remote_fetch | 0.94 |

### EVIDENCE_UNAVAILABLE

Phases **F1, F2, F4** and the **F8-close/PUB/INT** work have no archived Jev
exchange in `FIX_REPORT.md`: the raw request/response bodies beyond the
summaries above were not preserved. They are marked `EVIDENCE_UNAVAILABLE`
here on purpose — old phases are **not** re-run to fabricate a historical
answer. Their acceptance evidence is the test suites and logs only.

## 5. Question sharpenings (both versions retained)

Two questions were re-worded during the work. Both versions and both results
are listed; the change is part of the record, not hidden.

1. **F6f `bounded_subprocess_per_refresh`** — round 1: 0.49; round 2: 0.87.
   - Round 1 asked in terms of the call graph, which still textually calls
     `repo_heads(...)` from the fingerprints.
   - Round 2 clarified the question: count **spawned git subprocesses**, not
     function calls — `repo_heads` itself serves from the per-worktree TTL
     cache, so refreshes inside the TTL spawn zero subprocesses. That is the
     contract the tests (F6f-T01) actually verify.
2. **F7 `touches_local_path`** — rounds: 0.44 → 0.53 → 0.19.
   - The early wording conflated local-tab UI strings with accounting code.
   - The narrowed question asked only about the SQL/numeric arithmetic that
     produces local token totals, counting changed lines only. F7 changes no
     such line (its F7-T06 test asserts no inferred attribution).

No wording or threshold was tuned to manufacture a pass; the underlying
acceptance for every phase is the recorded RED/GREEN suites in `artifacts/`.

## 6. Limits of this evidence

- `noul` is model output about a text diff; it is not a test run.
- Truncation at 24 000 characters means the model did not see the tail of the
  largest diffs; the tests saw the full files.
- No prompt/response bodies were archived beyond the summaries in
  `FIX_REPORT.md`; where they are missing this file says
  `EVIDENCE_UNAVAILABLE` instead of inventing values.
