# Acceptance matrix (skeleton — all NOT_RUN)

Skeleton only. Every ID is `NOT_RUN` with no evidence. A gate passes
only when `tools/verify_acceptance.py` finds PASS rows in a real
`TEST_REPORT.json` with existing evidence files, matching code hash,
and per-result `windows`/`linux` environments. Workflow file presence
alone is not execution (`CI_PENDING` never counts as PASS).

Legend: `mandatory for core/release` uses `core+release` (mandatory for
both gates) for the full skeleton set pending triage; `core` would mean
core-gate mandatory, `release` release-only. `required environment` is
the OS coverage eventually required; per-result reports must still file
one row per single environment (`windows` or `linux`).

| acceptance_id | finding_id | test_file::test_name or runner/case | required environment | mandatory for core/release | result | evidence path |
| --- | --- | --- | --- | --- | --- | --- |
| F1-T01 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F1-T02 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F1-T03 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F1-T04 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F1-T05 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F1-T06 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F1-T07 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F1-T08 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F1-T09 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F1-T10 | F1 | tests/test_router_tokens.py::TestRouterTokens::test_router_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T01 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T02 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T03 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T04 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T05 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T06 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T07 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T08 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T09 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T10 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T11 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F2-T12 | F2 | tests/test_router_outcomes.py::TestRouterOutcomes::test_router_outcome_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F3-T01 | F3 | tests/test_local_regressions.py::TestLocalRegressions::test_day_total_and_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F3-T02 | F3 | tests/test_local_regressions.py::TestLocalRegressions::test_day_total_and_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F3-T03 | F3 | tests/test_local_regressions.py::TestLocalRegressions::test_day_total_and_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F3-T04 | F3 | tests/test_local_regressions.py::TestLocalRegressions::test_day_total_and_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F3-T05 | F3 | tests/test_local_regressions.py::TestLocalRegressions::test_day_total_and_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F3-T06 | F3 | tests/test_local_regressions.py::TestLocalRegressions::test_day_total_and_cache_rate_pure | windows+linux | core+release | NOT_RUN | - |
| F4-T01 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F4-T02 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F4-T03 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F4-T04 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F4-T05 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F4-T06 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F4-T07 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F4-T08 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F4-T09 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F4-T10 | F4 | tests/test_http_security.py::TestHttpSecurity::test_json_html_escapes_script_breakout | windows+linux | core+release | NOT_RUN | - |
| F5a-T01 | F5a | tests/test_codex_parsing.py::TestCodexParsing::test_numeric_and_model_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F5a-T02 | F5a | tests/test_codex_parsing.py::TestCodexParsing::test_numeric_and_model_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F5a-T03 | F5a | tests/test_codex_parsing.py::TestCodexParsing::test_numeric_and_model_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F5a-T04 | F5a | tests/test_codex_parsing.py::TestCodexParsing::test_numeric_and_model_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F5a-T05 | F5a | tests/test_codex_parsing.py::TestCodexParsing::test_numeric_and_model_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F5a-T06 | F5a | tests/test_codex_parsing.py::TestCodexParsing::test_numeric_and_model_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F5b-T01 | F5b | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| F5b-T02 | F5b | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| F5b-T03 | F5b | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| F5b-T04 | F5b | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| F5b-T05 | F5b | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| F5c-T01 | F5c | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F5c-T02 | F5c | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F5c-T03 | F5c | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F5c-T04 | F5c | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F5c-T05 | F5c | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6a-T01 | F6a | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6a-T02 | F6a | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6a-T03 | F6a | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6a-T04 | F6a | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6a-T05 | F6a | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6a-T06 | F6a | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6a-T07 | F6a | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6a-T08 | F6a | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6b-T01 | F6b | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6b-T02 | F6b | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6b-T03 | F6b | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6b-T04 | F6b | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6b-T05 | F6b | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6b-T06 | F6b | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6b-T07 | F6b | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6b-T08 | F6b | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T01 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T02 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T03 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T04 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T05 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T06 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T07 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T08 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T09 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6c-T10 | F6c | tests/test_commit_windows.py::TestCommitWindows::test_subsystem_for_file_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T01 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T02 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T03 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T04 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T05 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T06 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T07 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T08 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T09 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6d-T10 | F6d | tests/test_cache_and_git.py::TestCacheAndGit::test_pricing_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| F6e-T01 | F6e | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6e-T02 | F6e | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6e-T03 | F6e | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6e-T04 | F6e | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6e-T05 | F6e | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6e-T06 | F6e | tests/test_agent_activity.py::TestAgentActivity::test_streaks_and_duration_pure | windows+linux | core+release | NOT_RUN | - |
| F6f-T01 | F6f | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6f-T02 | F6f | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6f-T03 | F6f | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F6f-T04 | F6f | tests/test_inspector.py::TestInspector::test_inspector_display_helpers_pure | windows+linux | core+release | NOT_RUN | - |
| F7-T01 | F7 | tools/run_browser_tests.py::browser-case F7-T01 | windows+linux+browser | core+release | NOT_RUN | - |
| F7-T02 | F7 | tools/run_browser_tests.py::browser-case F7-T02 | windows+linux+browser | core+release | NOT_RUN | - |
| F7-T03 | F7 | tools/run_browser_tests.py::browser-case F7-T03 | windows+linux+browser | core+release | NOT_RUN | - |
| F7-T04 | F7 | tools/run_browser_tests.py::browser-case F7-T04 | windows+linux+browser | core+release | NOT_RUN | - |
| F7-T05 | F7 | tools/run_browser_tests.py::browser-case F7-T05 | windows+linux+browser | core+release | NOT_RUN | - |
| F7-T06 | F7 | tools/run_browser_tests.py::browser-case F7-T06 | windows+linux+browser | core+release | NOT_RUN | - |
| F8-T01 | F8 | tools/verify_acceptance.py::skeleton-case F8-T01 | windows+linux | core+release | NOT_RUN | - |
| F8-T02 | F8 | tools/verify_acceptance.py::skeleton-case F8-T02 | windows+linux | core+release | NOT_RUN | - |
| F8-T03 | F8 | tools/verify_acceptance.py::skeleton-case F8-T03 | windows+linux | core+release | NOT_RUN | - |
| F8-T04 | F8 | tools/verify_acceptance.py::skeleton-case F8-T04 | windows+linux | core+release | NOT_RUN | - |
| F8-T05 | F8 | tools/verify_acceptance.py::skeleton-case F8-T05 | windows+linux | core+release | NOT_RUN | - |
| F8-T06 | F8 | tools/verify_acceptance.py::skeleton-case F8-T06 | windows+linux | core+release | NOT_RUN | - |
| PUB-T01 | PUB | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| PUB-T02 | PUB | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| PUB-T03 | PUB | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| PUB-T04 | PUB | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| PUB-T05 | PUB | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| PUB-T06 | PUB | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| PUB-T07 | PUB | tests/test_codex_publication.py::TestCodexPublication::test_blank_stats_shape_and_safe_embed | windows+linux | core+release | NOT_RUN | - |
| INT-T01 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T02 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T03 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T04 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T05 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T06 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T07 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T08 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T09 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T10 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T11 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
| INT-T12 | INT | tests/test_codex_incremental.py::TestCodexIncremental::test_window_and_fingerprint_pure | windows+linux | core+release | NOT_RUN | - |
