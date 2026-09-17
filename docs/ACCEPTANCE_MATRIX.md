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
| F1-T01 | F1 | tests/test_router_tokens.py::TestRouterTokenContract::test_f1_01_contract_120_all_aggregates | windows+linux | core+release | PASS | artifacts/F1-GREEN.windows.log |
| F1-T02 | F1 | tests/test_router_tokens.py::TestRouterApiServe::test_f1_02_served_page_shape_120 | windows+linux | core+release | PASS | artifacts/F1-router-api.windows.log |
| F1-T03 | F1 | tests/test_router_tokens.py::TestRouterFrontendContract::test_f1_03_frontend_js_totals_via_node | windows+linux | core+release | PASS | artifacts/F1-GREEN.windows.log |
| F1-T04 | F1 | tests/test_router_tokens.py::TestRouterTokenContract::test_f1_04_cache_rate_40 | windows+linux | core+release | PASS | artifacts/F1-GREEN.windows.log |
| F1-T05 | F1 | tests/test_router_tokens.py::TestRouterTokenContract::test_f1_05_multi_record_consistency_210 | windows+linux | core+release | PASS | artifacts/F1-GREEN.windows.log |
| F1-T06 | F1 | tests/test_router_tokens.py::TestRouterTokenContract::test_f1_06_missing_total_fallback | windows+linux | core+release | PASS | artifacts/F1-GREEN.windows.log |
| F1-T07 | F1 | tests/test_router_tokens.py::TestRouterTokenContract::test_f1_07_zeros_partial_conflict | windows+linux | core+release | PASS | artifacts/F1-GREEN.windows.log |
| F1-T08 | F1 | tests/test_router_tokens.py::TestRouterTokenContract::test_f1_08_invalid_values_sanitized | windows+linux | core+release | PASS | artifacts/F1-GREEN.windows.log |
| F1-T09 | F1 | tests/test_router_tokens.py::TestRouterTokenContract::test_f1_09_local_path_unchanged | windows+linux | core+release | PASS | artifacts/F1-GREEN.windows.log |
| F1-T10 | F1 | tests/test_router_tokens.py::TestRouterTokenContract::test_f1_10_odd_status_still_counts_usage | windows+linux | core+release | PASS | artifacts/F1-GREEN.windows.log |
| F2-T01 | F2 | tests/test_http_security.py::TestF2T01PrivateRoutesRequireToken::test_f2_t01_no_wrong_empty_multi_token_is_401 | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T02 | F2 | tests/test_http_security.py::TestF2T02ValidTokenAndHostWorks::test_f2_t02_canonical_host_and_origin_ok | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T03 | F2 | tests/test_http_security.py::TestF2T03ForeignHostRejected::test_f2_t03_foreign_host_with_matching_origin_is_403 | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T04 | F2 | tests/test_http_security.py::TestF2T04WrongOriginRejected::test_f2_t04_foreign_or_null_origin_is_403 | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T05 | F2 | tests/test_http_security.py::TestF2T05BadHostShapes::test_f2_t05_missing_double_foreign_suffix_userinfo | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T06 | F2 | tests/test_http_security.py::TestF2T06PublicShellHasNoPrivateData::test_f2_t06_shell_without_token_has_no_canary_or_secret | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T07 | F2 | tests/test_http_security.py::TestF2T07NoBypassViaMethodOrPath::test_f2_t07_head_options_unknown_need_auth_and_no_effects | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T08 | F2 | tests/test_http_security.py::TestF2T08FragmentTokenFlow::test_f2_t08_no_query_token_and_js_fragment_handling | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T09 | F2 | tests/test_http_security.py::TestF2T09TwoWindowsCloseIsolation::test_f2_t09_one_window_close_keeps_other_alive | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T10 | F2 | tests/test_http_security.py::TestF2T10RestartInvalidatesOldToken::test_f2_t10_old_token_is_401_and_new_link_recovers | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T11 | F2 | tests/test_http_security.py::TestF2T11SecondLocalOriginRejected::test_f2_t11_other_local_origin_gets_no_canary_and_no_close | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F2-T12 | F2 | tests/test_http_security.py::TestF2T12SecurityHeadersAndNoLeak::test_f2_t12_headers_present_and_denials_generic | windows+linux | core+release | PASS | artifacts/F2-GREEN.windows.log |
| F3-T01 | F3 | tests/test_local_regressions.py::TestF3ActivityStates::test_f3_t01_age_boundaries_exact | windows | core+release | PASS | artifacts/F3-GREEN.windows.log |
| F3-T02 | F3 | tests/test_local_regressions.py::TestF3ActivityStates::test_f3_t02_invalid_timestamps_are_unknown | windows | core+release | PASS | artifacts/F3-GREEN.windows.log |
| F3-T03 | F3 | tests/test_local_regressions.py::TestF3ActivityStates::test_f3_t03_limit_counters_and_truncation | windows | core+release | PASS | artifacts/F3-GREEN.windows.log |
| F3-T04 | F3 | tests/test_local_regressions.py::TestF3ActivityStates::test_f3_t04_time_passes_without_db_writes | windows | core+release | PASS | artifacts/F3-GREEN.windows.log |
| F3-T05 | F3 | tests/test_local_regressions.py::TestF3ActivityStates::test_f3_t05_no_execution_state_claims | windows | core+release | PASS | artifacts/F3-GREEN.windows.log |
| F3-T06 | F3 | tests/test_local_regressions.py::TestF3ActivityStates::test_f3_t06_ui_and_readme_use_activity_language | windows | core+release | PASS | artifacts/F3-GREEN.windows.log |
| F4-T01 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t01_synth_single_event_unknown_120 | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F4-T02 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t02_bad_statuses_are_unknown_not_success | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F4-T03 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t03_real_statuses_classified | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F4-T04 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t04_mixed_tokens_150 | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F4-T05 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t05_success_rate_over_known_with_coverage | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F4-T06 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t06_unknown_tokens_everywhere | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F4-T07 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t07_zero_vs_missing_measurement | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F4-T08 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t08_old_synth_200_invalidated | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F4-T09 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t09_synth_latency_na | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F4-T10 | F4 | tests/test_router_outcomes.py::TestF4UnknownOutcomes::test_f4_t10_ui_unknown_exports_and_authed_api | windows | core+release | PASS | artifacts/F4-GREEN.windows.log |
| F5a-T01 | F5a | tests/test_synth_publish.py::TestSynthPublish::test_f5a_t01_failure_then_success | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F5a-T02 | F5a | tests/test_synth_publish.py::TestSynthPublish::test_f5a_t02_failed_republish_keeps_old | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F5a-T03 | F5a | tests/test_synth_publish.py::TestSynthPublish::test_f5a_t03_no_previous_failure_is_explicit | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F5a-T04 | F5a | tests/test_synth_publish.py::TestSynthPublish::test_f5a_t04_deleted_index_rebuilds | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F5a-T05 | F5a | tests/test_synth_publish.py::TestSynthPublish::test_f5a_t05_schema_bump_rebuilds | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F5a-T06 | F5a | tests/test_synth_publish.py::TestSynthPublish::test_f5a_t06_no_retry_loop_readable_state | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F5b-T01 | F5b | tests/test_codex_publication.py::TestF5bEnumeration::test_f5b_t01_nested_rollout_seen_by_diagnostics_and_readers | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F5b-T02 | F5b | tests/test_codex_publication.py::TestF5bEnumeration::test_f5b_t02_empty_dir_vs_missing_dir_distinct | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F5b-T03 | F5b | tests/test_codex_publication.py::TestF5bEnumeration::test_f5b_t03_foreign_jsonl_does_not_inflate_counts | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F5b-T04 | F5b | tests/test_codex_publication.py::TestF5bEnumeration::test_f5b_t04_injected_failures_are_explicit_not_clean | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F5b-T05 | F5b | tests/test_codex_publication.py::TestF5bEnumeration::test_f5b_t05_added_removed_files_refresh_consumers | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F5c-T01 | F5c | tests/test_codex_incremental.py::TestF5cRecGuard::test_f5c_t01_non_object_json_lines_rejected | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F5c-T02 | F5c | tests/test_codex_incremental.py::TestF5cRecGuard::test_f5c_t02_bad_json_empty_line_and_usable_record | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F5c-T03 | F5c | tests/test_codex_incremental.py::TestF5cRecGuard::test_f5c_t03_payload_guard_still_handles_wrong_payload | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F5c-T04 | F5c | tests/test_codex_incremental.py::TestF5cRecGuard::test_f5c_t04_standard_file_semantics_unchanged | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F5c-T05 | F5c | tests/test_codex_incremental.py::TestF5cRecGuard::test_f5c_t05_api_codex_over_mixed_fixture_no_500 | windows | core+release | PASS | artifacts/F5b-F5c-GREEN.windows.log |
| F6a-T01 | F6a | tests/test_synth_publish.py::TestSynthPublish::test_f6a_t01_readers_see_complete_generations | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F6a-T02 | F6a | tests/test_synth_publish.py::TestSynthPublish::test_f6a_t02_release_then_complete | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F6a-T03 | F6a | tests/test_synth_publish.py::TestSynthPublish::test_f6a_t03_concurrent_publish_no_corruption | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F6a-T04 | F6a | tests/test_synth_publish.py::TestSynthPublish::test_f6a_t04_failure_stage_keeps_old | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F6a-T05 | F6a | tests/test_synth_publish.py::TestSynthPublish::test_f6a_t05_single_generation_aggregates | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F6a-T06 | F6a | tests/test_synth_publish.py::TestSynthPublish::test_f6a_t06_active_reader_no_torn_file | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F6a-T07 | F6a | tests/test_synth_publish.py::TestSynthPublish::test_f6a_t07_no_shared_payload_mutation | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F6a-T08 | F6a | tests/test_synth_publish.py::TestSynthPublish::test_f6a_t08_restart_keeps_old_spares_foreign | windows | core+release | PASS | artifacts/F5a-F6a-GREEN.windows.log |
| F6b-T01 | F6b | tests/test_router_events.py::TestF6bRouterEvents::test_f6b_t01_large_file_returns_bounded_tail | windows | core+release | PASS | artifacts/F6b-GREEN.windows.log |
| F6b-T02 | F6b | tests/test_router_events.py::TestF6bRouterEvents::test_f6b_t02_tail_never_readlines_and_warm_read_is_cheap | windows | core+release | PASS | artifacts/F6b-GREEN.windows.log |
| F6b-T03 | F6b | tests/test_router_events.py::TestF6bRouterEvents::test_f6b_t03_synthesis_keeps_full_history_no_30000_cut | windows | core+release | PASS | artifacts/F6b-GREEN.windows.log |
| F6b-T04 | F6b | tests/test_router_events.py::TestF6bRouterEvents::test_f6b_t04_crlf_unicode_and_block_boundaries_match_reference | windows | core+release | PASS | artifacts/F6b-GREEN.windows.log |
| F6b-T05 | F6b | tests/test_router_events.py::TestF6bRouterEvents::test_f6b_t05_oversize_line_is_counted_and_next_record_survives | windows | core+release | PASS | artifacts/F6b-GREEN.windows.log |
| F6b-T06 | F6b | tests/test_router_events.py::TestF6bRouterEvents::test_f6b_t06_unterminated_tail_is_pending_then_read_once | windows | core+release | PASS | artifacts/F6b-GREEN.windows.log |
| F6b-T07 | F6b | tests/test_router_events.py::TestF6bRouterEvents::test_f6b_t07_append_truncate_rotation_invalidate_counts | windows | core+release | PASS | artifacts/F6b-GREEN.windows.log |
| F6b-T08 | F6b | tests/test_router_events.py::TestF6bRouterEvents::test_f6b_t08_non_chronological_tail_scope_and_recent_sort | windows | core+release | PASS | artifacts/F6b-GREEN.windows.log |
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
| F7-T01 | F7 | tests/test_commit_attribution.py::TestF7CommitWindows::test_f7_t01_two_commits_share_window_not_additive | windows | core+release | PASS | artifacts/F7-GREEN.windows.log |
| F7-T02 | F7 | tests/test_commit_attribution.py::TestF7CommitWindows::test_f7_t02_two_repos_global_scope | windows | core+release | PASS | artifacts/F7-GREEN.windows.log |
| F7-T03 | F7 | tests/test_commit_attribution.py::TestF7CommitWindows::test_f7_t03_window_boundaries_inclusive | windows | core+release | PASS | artifacts/F7-GREEN.windows.log |
| F7-T04 | F7 | tests/test_commit_attribution.py::TestF7CommitWindows::test_f7_t04_timezone_equivalence | windows | core+release | PASS | artifacts/F7-GREEN.windows.log |
| F7-T05 | F7 | tests/test_commit_attribution.py::TestF7CommitWindows::test_f7_t05_warnings_in_ui_and_export | windows | core+release | PASS | artifacts/F7-GREEN.windows.log |
| F7-T06 | F7 | tests/test_commit_attribution.py::TestF7CommitWindows::test_f7_t06_no_inferred_attribution | windows | core+release | PASS | artifacts/F7-GREEN.windows.log |
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
