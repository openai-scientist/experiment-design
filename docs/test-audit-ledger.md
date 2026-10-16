# Original declaration inventory and conservative retention ledger

Every original declaration is R. Contract text below is its declared behavior; unreviewed/doubtful entries are conservatively retained, not certified by assertion extraction. Reviewed candidate evidence and audit limits are in [test-audit.md](test-audit.md).

Baseline results include expanded parameter rows and per-case JUnit time (setup/call/teardown). Wall time and contention are discussed in the report. Every original file has exactly one ownership group by its directory.

| Original test file / owner directory | Passed | Skipped | Failed | Summed case seconds |
|---|---:|---:|---:|---:|
| `tests/contract/test_contract_suite.py` | 19 | 1 | 0 | 89.665 |
| `tests/contract/test_wire_models.py` | 67 | 0 | 0 | 0.744 |
| `tests/e2e/test_filesystem_commands.py` | 17 | 0 | 0 | 9.490 |
| `tests/integration/api/test_app.py` | 22 | 0 | 0 | 6.951 |
| `tests/integration/api/test_callbacks.py` | 13 | 0 | 0 | 6.556 |
| `tests/integration/api/test_incomplete_gate.py` | 11 | 0 | 0 | 43.452 |
| `tests/integration/api/test_inputs.py` | 9 | 0 | 0 | 0.128 |
| `tests/integration/api/test_question_gate.py` | 2 | 0 | 0 | 8.111 |
| `tests/integration/api/test_revision_allowance.py` | 2 | 0 | 0 | 12.913 |
| `tests/integration/api/test_revision_artifacts.py` | 1 | 0 | 0 | 6.177 |
| `tests/integration/api/test_run_manager.py` | 22 | 0 | 0 | 75.939 |
| `tests/integration/persistence/test_envelope_log.py` | 9 | 0 | 0 | 2.375 |
| `tests/integration/persistence/test_filesystem_store.py` | 27 | 0 | 0 | 2.341 |
| `tests/integration/publication/test_exact_markdown.py` | 7 | 0 | 0 | 0.079 |
| `tests/integration/review/test_api_hooks.py` | 11 | 0 | 0 | 27.419 |
| `tests/integration/review/test_markdown_workflow.py` | 48 | 0 | 0 | 181.709 |
| `tests/integration/review/test_mlse_workflow.py` | 2 | 0 | 0 | 7.381 |
| `tests/integration/review/test_multi_hypothesis_prompt.py` | 1 | 0 | 0 | 3.326 |
| `tests/integration/review/test_resources_gate.py` | 5 | 0 | 0 | 16.694 |
| `tests/integration/review/test_review_fixes.py` | 28 | 0 | 0 | 85.592 |
| `tests/integration/review/test_secondary_review.py` | 3 | 0 | 0 | 0.694 |
| `tests/integration/review/test_workspace_agent.py` | 31 | 0 | 0 | 32.234 |
| `tests/unit/agents/test_contract_prompts.py` | 6 | 0 | 0 | 0.995 |
| `tests/unit/agents/test_prompts_pack.py` | 2 | 0 | 0 | 0.018 |
| `tests/unit/api/test_activity.py` | 10 | 0 | 0 | 0.898 |
| `tests/unit/api/test_dependencies.py` | 2 | 0 | 0 | 0.066 |
| `tests/unit/api/test_findings.py` | 5 | 0 | 0 | 4.273 |
| `tests/unit/api/test_mapping.py` | 16 | 0 | 0 | 0.032 |
| `tests/unit/api/test_observability.py` | 2 | 0 | 0 | 0.004 |
| `tests/unit/api/test_projector.py` | 14 | 0 | 0 | 1.566 |
| `tests/unit/benchmark/test_hf.py` | 8 | 0 | 0 | 0.028 |
| `tests/unit/benchmark/test_registry.py` | 2 | 0 | 0 | 0.033 |
| `tests/unit/benchmark/test_step.py` | 4 | 0 | 0 | 1.391 |
| `tests/unit/benchmark/test_survey_select.py` | 7 | 0 | 0 | 1.169 |
| `tests/unit/benchmark/test_validate.py` | 168 | 0 | 0 | 0.427 |
| `tests/unit/packs/test_content.py` | 14 | 0 | 0 | 0.224 |
| `tests/unit/packs/test_examples.py` | 4 | 0 | 0 | 0.010 |
| `tests/unit/packs/test_infer.py` | 5 | 0 | 0 | 0.678 |
| `tests/unit/packs/test_loader.py` | 8 | 0 | 0 | 0.329 |
| `tests/unit/packs/test_validators.py` | 8 | 0 | 0 | 0.017 |
| `tests/unit/publication/test_check_index.py` | 7 | 0 | 0 | 0.011 |
| `tests/unit/publication/test_experiment_index.py` | 4 | 0 | 0 | 0.494 |
| `tests/unit/runtime/test_configuration.py` | 10 | 0 | 0 | 0.119 |
| `tests/unit/runtime/test_judge_provider.py` | 14 | 0 | 0 | 0.026 |
| `tests/unit/runtime/test_llm_provider.py` | 21 | 0 | 0 | 0.219 |
| `tests/unit/runtime/test_request_providers.py` | 10 | 0 | 0 | 0.090 |
| `tests/unit/science/test_calculations.py` | 27 | 0 | 0 | 0.109 |
| `tests/unit/science/test_evidence_lookup.py` | 1 | 0 | 0 | 0.001 |
| `tests/unit/science/test_registry.py` | 9 | 0 | 0 | 1.868 |
| `tests/unit/science/test_stats.py` | 30 | 0 | 0 | 0.187 |
| `tests/unit/test_activity.py` | 1 | 0 | 0 | 0.127 |

## R declarations

| Location | Contract / failure guarded (declaration inventory) | Mark |
|---|---|---|
| `tests/contract/test_contract_suite.py:50::test_capabilities_valid` | capabilities valid | R |
| `tests/contract/test_contract_suite.py:61::test_runs_with_base_profile` | runs with base profile | R |
| `tests/contract/test_contract_suite.py:68::test_health_liveness_without_key` | health liveness without key | R |
| `tests/contract/test_contract_suite.py:80::test_runs_require_service_key` | runs require service key | R |
| `tests/contract/test_contract_suite.py:87::test_create_run_idempotent` | create run idempotent | R |
| `tests/contract/test_contract_suite.py:98::test_reaches_review_gate` | reaches review gate | R |
| `tests/contract/test_contract_suite.py:106::test_lifecycle_events_ordered` | lifecycle events ordered | R |
| `tests/contract/test_contract_suite.py:119::test_activity_events_declared` | activity events declared | R |
| `tests/contract/test_contract_suite.py:130::test_review_uses_review_model` | review uses review model | R |
| `tests/contract/test_contract_suite.py:137::test_comment_returns_to_review_gate` | comment returns to review gate | R |
| `tests/contract/test_contract_suite.py:150::test_approve_publishes_primary_artifact` | approve publishes primary artifact | R |
| `tests/contract/test_contract_suite.py:166::test_cancel` | cancel | R |
| `tests/contract/test_contract_suite.py:175::test_no_secret_leak` | no secret leak | R |
| `tests/contract/test_contract_suite.py:197::test_telemetry_well_formed` | telemetry well formed | R |
| `tests/contract/test_contract_suite.py:233::test_restart_fails_open_runs` | restart fails open runs | R |
| `tests/contract/test_contract_suite.py:237::test_pause_then_resume_continues` | pause then resume continues | R |
| `tests/contract/test_contract_suite.py:249::test_error_body_shape` | error body shape | R |
| `tests/contract/test_contract_suite.py:259::test_event_gap_triggers_resend_not_cancel` | event gap triggers resend not cancel | R |
| `tests/contract/test_contract_suite.py:272::test_repeated_gate_answer_is_idempotent` | repeated gate answer is idempotent | R |
| `tests/contract/test_contract_suite.py:288::test_validation_error_does_not_echo_api_key` | validation error does not echo api key | R |
| `tests/contract/test_wire_models.py:41::test_contract_version_matches_vendored_readme` | contract version matches vendored readme | R |
| `tests/contract/test_wire_models.py:47::test_every_vendored_schema_has_an_engine_model` | every vendored schema has an engine model | R |
| `tests/contract/test_wire_models.py:52::test_example_parses_and_round_trips_through_schema` | example parses and round trips through schema | R |
| `tests/contract/test_wire_models.py:60::test_event_example_parses_and_payload_matches_type` | event example parses and payload matches type | R |
| `tests/contract/test_wire_models.py:70::test_every_schema_has_an_example` | every schema has an example | R |
| `tests/contract/test_wire_models.py:75::test_receivers_ignore_unknown_fields` | receivers ignore unknown fields | R |
| `tests/contract/test_wire_models.py:81::test_secret_and_telemetry_models_reject_unknown_fields` | secret and telemetry models reject unknown fields | R |
| `tests/contract/test_wire_models.py:87::test_review_model_must_differ_from_stage_model` | review model must differ from stage model | R |
| `tests/contract/test_wire_models.py:94::test_secret_values_are_not_dumped` | secret values are not dumped | R |
| `tests/contract/test_wire_models.py:103::test_vendor_matches_management` | vendor matches management | R |
| `tests/e2e/test_filesystem_commands.py:25::test_offline_commands_need_no_config_or_keys` | offline commands need no config or keys | R |
| `tests/e2e/test_filesystem_commands.py:37::test_decide_forwards_actual_identity_text_and_revision` | decide forwards actual identity text and revision | R |
| `tests/e2e/test_filesystem_commands.py:74::test_sqlite_path_is_clear_error` | sqlite path is clear error | R |
| `tests/e2e/test_filesystem_commands.py:81::test_budget_increase_preserves_usage_and_requires_reason` | budget increase preserves usage and requires reason | R |
| `tests/e2e/test_filesystem_commands.py:92::test_invalid_increase_preserves_state` | invalid increase preserves state | R |
| `tests/e2e/test_filesystem_commands.py:100::test_unknown_usage_cannot_be_reset_by_ceiling_increase` | unknown usage cannot be reset by ceiling increase | R |
| `tests/e2e/test_filesystem_commands.py:108::test_resume_with_invalid_config_does_not_persist_budget_increase` | resume with invalid config does not persist budget increase | R |
| `tests/e2e/test_filesystem_commands.py:121::test_status_counts_explanations_for_current_plan` | status counts explanations for current plan | R |
| `tests/e2e/test_filesystem_commands.py:130::test_status_does_not_claim_stale_checkpoint_approval` | status does not claim stale checkpoint approval | R |
| `tests/e2e/test_filesystem_commands.py:139::test_real_cli_run_then_offline_review_and_approval` | real cli run then offline review and approval | R |
| `tests/e2e/test_filesystem_commands.py:219::test_status_and_review_show_numbered_options` | status and review show numbered options | R |
| `tests/e2e/test_filesystem_commands.py:231::test_decide_option_records_exact_option_text` | decide option records exact option text | R |
| `tests/e2e/test_filesystem_commands.py:243::test_decide_option_out_of_range_is_a_clear_error` | decide option out of range is a clear error | R |
| `tests/e2e/test_filesystem_commands.py:252::test_decide_help_lists_option` | decide help lists option | R |
| `tests/e2e/test_filesystem_commands.py:275::test_resume_persists_changed_run_limits_before_advancing` | resume persists changed run limits before advancing | R |
| `tests/e2e/test_filesystem_commands.py:283::test_resume_with_unchanged_limits_changes_nothing` | resume with unchanged limits changes nothing | R |
| `tests/integration/api/test_app.py:24::test_health_is_public_and_detailed_with_key` | health is public and detailed with key | R |
| `tests/integration/api/test_app.py:34::test_capabilities_valid_and_keyed` | capabilities valid and keyed | R |
| `tests/integration/api/test_app.py:59::test_every_route_but_health_needs_the_service_key` | every route but health needs the service key | R |
| `tests/integration/api/test_app.py:66::test_create_run_and_duplicate` | create run and duplicate | R |
| `tests/integration/api/test_app.py:96::test_error_bodies_have_code_and_message` | error bodies have code and message | R |
| `tests/integration/api/test_app.py:105::test_validation_error_does_not_echo_api_key` | validation error does not echo api key | R |
| `tests/integration/api/test_app.py:117::test_service_key_is_compared_in_constant_time` | service key is compared in constant time | R |
| `tests/integration/api/test_app.py:126::test_secret_filter_masks_message_args_and_exceptions` | secret filter masks message args and exceptions | R |
| `tests/integration/api/test_app.py:141::test_created_run_registers_its_keys_with_the_log_filter` | created run registers its keys with the log filter | R |
| `tests/integration/api/test_callbacks.py:41::test_events_delivered_in_order` | events delivered in order | R |
| `tests/integration/api/test_callbacks.py:49::test_large_backlog_is_sent_in_batches` | large backlog is sent in batches | R |
| `tests/integration/api/test_callbacks.py:56::test_retry_after_503_does_not_duplicate` | retry after 503 does not duplicate | R |
| `tests/integration/api/test_callbacks.py:63::test_lost_acknowledgement_is_resent_without_duplicates` | lost acknowledgement is resent without duplicates | R |
| `tests/integration/api/test_callbacks.py:70::test_retries_are_bounded` | retries are bounded | R |
| `tests/integration/api/test_callbacks.py:79::test_network_error_is_retried` | network error is retried | R |
| `tests/integration/api/test_callbacks.py:95::test_event_gap_resends_from_the_platform_cursor_and_keeps_the_run` | event gap resends from the platform cursor and keeps the run | R |
| `tests/integration/api/test_callbacks.py:103::test_stage_closed_stops_delivery` | stage closed stops delivery | R |
| `tests/integration/api/test_callbacks.py:111::test_wrong_service_key_is_rejected_without_retry` | wrong service key is rejected without retry | R |
| `tests/integration/api/test_callbacks.py:136::test_artifact_roundtrip` | artifact roundtrip | R |
| `tests/integration/api/test_callbacks.py:147::test_artifact_retried_after_server_error` | artifact retried after server error | R |
| `tests/integration/api/test_callbacks.py:154::test_artifact_hash_mismatch_is_not_retried` | artifact hash mismatch is not retried | R |
| `tests/integration/api/test_callbacks.py:161::test_telemetry_is_delivered` | telemetry is delivered | R |
| `tests/integration/api/test_incomplete_gate.py:61::test_incomplete_opens_gate` | incomplete opens gate | R |
| `tests/integration/api/test_incomplete_gate.py:75::test_incomplete_without_final_review_reports_the_error` | incomplete without final review reports the error | R |
| `tests/integration/api/test_incomplete_gate.py:86::test_incomplete_waits_for_researcher` | incomplete waits for researcher | R |
| `tests/integration/api/test_incomplete_gate.py:95::test_auto_does_not_approve_incomplete` | auto does not approve incomplete | R |
| `tests/integration/api/test_incomplete_gate.py:106::test_approve_incomplete_records_open_findings` | approve incomplete records open findings | R |
| `tests/integration/api/test_incomplete_gate.py:132::test_comment_at_incomplete_runs_one_more_revision` | comment at incomplete runs one more revision | R |
| `tests/integration/api/test_incomplete_gate.py:148::test_cli_incomplete_guard_unchanged` | cli incomplete guard unchanged | R |
| `tests/integration/api/test_incomplete_gate.py:186::test_approve_after_reviewer_failure_publishes` | approve after reviewer failure publishes | R |
| `tests/integration/api/test_incomplete_gate.py:194::test_comment_after_reviewer_failure_is_accepted` | comment after reviewer failure is accepted | R |
| `tests/integration/api/test_incomplete_gate.py:226::test_reviewer_failure_on_re_review_fails_the_run_instead_of_hanging` | reviewer failure on re review fails the run instead of hanging | R |
| `tests/integration/api/test_inputs.py:18::test_selection_keeps_only_chosen` | selection keeps only chosen | R |
| `tests/integration/api/test_inputs.py:28::test_no_selection_keeps_every_hypothesis_with_its_fields` | no selection keeps every hypothesis with its fields | R |
| `tests/integration/api/test_inputs.py:39::test_hash_mismatch_rejected` | hash mismatch rejected | R |
| `tests/integration/api/test_inputs.py:48::test_unknown_selection_rejected` | unknown selection rejected | R |
| `tests/integration/api/test_inputs.py:55::test_exactly_one_hypotheses_artifact_required` | exactly one hypotheses artifact required | R |
| `tests/integration/api/test_inputs.py:63::test_download_requires_the_service_key` | download requires the service key | R |
| `tests/integration/api/test_inputs.py:74::test_malformed_hypotheses_rejected` | malformed hypotheses rejected | R |
| `tests/integration/api/test_question_gate.py:45::test_copilot_question_gate_and_exact_answer_resume` | copilot question gate and exact answer resume | R |
| `tests/integration/api/test_question_gate.py:88::test_light_uses_actual_default_without_opening_question_gate` | light uses actual default without opening question gate | R |
| `tests/integration/api/test_revision_allowance.py:9::test_researcher_comment_has_its_own_revision_allowance` | researcher comment has its own revision allowance | R |
| `tests/integration/api/test_revision_allowance.py:28::test_insufficient_token_reserve_preserves_gate_and_allowance` | insufficient token reserve preserves gate and allowance | R |
| `tests/integration/api/test_revision_artifacts.py:27::test_revision_diff_response_and_plan_bindings_are_delivered` | revision diff response and plan bindings are delivered | R |
| `tests/integration/api/test_run_manager.py:78::test_light_run_reaches_review_gate` | light run reaches review gate | R |
| `tests/integration/api/test_run_manager.py:96::test_create_is_idempotent` | create is idempotent | R |
| `tests/integration/api/test_run_manager.py:104::test_approve_publishes_primary` | approve publishes primary | R |
| `tests/integration/api/test_run_manager.py:130::test_comment_goes_back_to_review_gate` | comment goes back to review gate | R |
| `tests/integration/api/test_run_manager.py:153::test_system_actor_is_recorded_as_system` | system actor is recorded as system | R |
| `tests/integration/api/test_run_manager.py:162::test_missing_typesafe_integration_rejected` | missing typesafe integration rejected | R |
| `tests/integration/api/test_run_manager.py:170::test_unsupported_provider_rejected` | unsupported provider rejected | R |
| `tests/integration/api/test_run_manager.py:178::test_bad_hash_input_rejected_with_contract_code` | bad hash input rejected with contract code | R |
| `tests/integration/api/test_run_manager.py:187::test_no_key_on_disk` | no key on disk | R |
| `tests/integration/api/test_run_manager.py:204::test_review_model_only_used_while_reviewing` | review model only used while reviewing | R |
| `tests/integration/api/test_run_manager.py:212::test_llm_error_fails_run` | llm error fails run | R |
| `tests/integration/api/test_run_manager.py:237::test_escaping_exceptions_fail_the_run_instead_of_killing_the_worker` | escaping exceptions fail the run instead of killing the worker | R |
| `tests/integration/api/test_run_manager.py:254::test_workflow_stop_is_announced_as_failure` | workflow stop is announced as failure | R |
| `tests/integration/api/test_run_manager.py:265::test_cancel_idle_run_at_gate` | cancel idle run at gate | R |
| `tests/integration/api/test_run_manager.py:278::test_cancel_running_run_stops_the_worker` | cancel running run stops the worker | R |
| `tests/integration/api/test_run_manager.py:299::test_pause_then_resume_continues` | pause then resume continues | R |
| `tests/integration/api/test_run_manager.py:320::test_pause_at_gate_keeps_gate_and_resume_restores_it` | pause at gate keeps gate and resume restores it | R |
| `tests/integration/api/test_run_manager.py:335::test_unopened_and_repeated_gate_answers` | unopened and repeated gate answers | R |
| `tests/integration/api/test_run_manager.py:363::test_stage_closed_by_platform_cancels_the_run` | stage closed by platform cancels the run | R |
| `tests/integration/api/test_run_manager.py:375::test_questions_are_answered_for_the_system_in_light_mode` | questions are answered for the system in light mode | R |
| `tests/integration/persistence/test_envelope_log.py:21::test_seq_contiguous_across_reopen` | seq contiguous across reopen | R |
| `tests/integration/persistence/test_envelope_log.py:29::test_activity_requires_summary` | activity requires summary | R |
| `tests/integration/persistence/test_envelope_log.py:41::test_lifecycle_conditions_are_enforced` | lifecycle conditions are enforced | R |
| `tests/integration/persistence/test_envelope_log.py:56::test_store_listener_sees_event_and_state` | store listener sees event and state | R |
| `tests/integration/persistence/test_envelope_log.py:65::test_store_listener_errors_propagate` | store listener errors propagate | R |
| `tests/integration/persistence/test_envelope_log.py:76::test_delivered_cursor_persists_and_never_moves_back` | delivered cursor persists and never moves back | R |
| `tests/integration/persistence/test_envelope_log.py:83::test_concurrent_append_keeps_seq_contiguous` | concurrent append keeps seq contiguous | R |
| `tests/integration/persistence/test_envelope_log.py:97::test_torn_final_append_is_dropped_and_sequence_stays_contiguous` | torn final append is dropped and sequence stays contiguous | R |
| `tests/integration/persistence/test_envelope_log.py:107::test_complete_record_missing_its_newline_is_kept` | complete record missing its newline is kept | R |
| `tests/integration/persistence/test_filesystem_store.py:16::test_exact_snapshot_and_dependency_tampering` | exact snapshot and dependency tampering | R |
| `tests/integration/persistence/test_filesystem_store.py:30::test_archive_preserves_usage_responses_and_clears_review` | archive preserves usage responses and clears review | R |
| `tests/integration/persistence/test_filesystem_store.py:55::test_artifact_paths_cannot_escape` | artifact paths cannot escape | R |
| `tests/integration/persistence/test_filesystem_store.py:60::test_execution_lock_rejects_overlap_and_releases` | execution lock rejects overlap and releases | R |
| `tests/integration/persistence/test_filesystem_store.py:72::test_missing_outputs_cannot_complete` | missing outputs cannot complete | R |
| `tests/integration/persistence/test_filesystem_store.py:79::test_truncated_last_event_recovers_without_losing_prior_event` | truncated last event recovers without losing prior event | R |
| `tests/integration/persistence/test_filesystem_store.py:88::test_manifest_cannot_authorize_unlisted_dependency` | manifest cannot authorize unlisted dependency | R |
| `tests/integration/persistence/test_filesystem_store.py:98::test_archive_crash_recovers_correct_stage_and_attempt` | archive crash recovers correct stage and attempt | R |
| `tests/integration/persistence/test_filesystem_store.py:124::test_multiple_source_names_are_unique_and_manifest_binds_them` | multiple source names are unique and manifest binds them | R |
| `tests/integration/persistence/test_filesystem_store.py:141::test_missing_intake_inputs_are_rejected` | missing intake inputs are rejected | R |
| `tests/integration/persistence/test_filesystem_store.py:146::test_duplicate_run_directory_is_rejected` | duplicate run directory is rejected | R |
| `tests/integration/persistence/test_filesystem_store.py:163::test_artifact_alias_cannot_overwrite_manifest_on_windows` | artifact alias cannot overwrite manifest on windows | R |
| `tests/integration/persistence/test_filesystem_store.py:171::test_case_alias_cannot_write_or_declare_manifest` | case alias cannot write or declare manifest | R |
| `tests/integration/persistence/test_filesystem_store.py:180::test_source_index_tamper_cannot_omit_additional_input` | source index tamper cannot omit additional input | R |
| `tests/integration/persistence/test_filesystem_store.py:197::test_constructor_cannot_recover_an_active_archive` | constructor cannot recover an active archive | R |
| `tests/integration/persistence/test_filesystem_store.py:219::test_event_appends_without_rewriting_journal` | event appends without rewriting journal | R |
| `tests/integration/persistence/test_filesystem_store.py:237::test_torn_final_line_is_ignored_but_middle_corruption_is_an_error` | torn final line is ignored but middle corruption is an error | R |
| `tests/integration/persistence/test_filesystem_store.py:249::test_atomic_write_retries_transient_permission_error` | atomic write retries transient permission error | R |
| `tests/integration/persistence/test_filesystem_store.py:268::test_atomic_write_reports_persistent_permission_error` | atomic write reports persistent permission error | R |
| `tests/integration/persistence/test_filesystem_store.py:281::test_non_utf8_input_is_rejected_before_run_directory_exists` | non utf8 input is rejected before run directory exists | R |
| `tests/integration/persistence/test_filesystem_store.py:290::test_utf8_bom_and_newlines_stay_exact` | utf8 bom and newlines stay exact | R |
| `tests/integration/publication/test_exact_markdown.py:55::test_pending_acceptance_publishes_exact_bytes` | pending acceptance publishes exact bytes | R |
| `tests/integration/publication/test_exact_markdown.py:63::test_incomplete_requires_explicit_draft_and_exposes_gaps` | incomplete requires explicit draft and exposes gaps | R |
| `tests/integration/publication/test_exact_markdown.py:73::test_stale_document_or_review_cannot_publish_complete` | stale document or review cannot publish complete | R |
| `tests/integration/publication/test_exact_markdown.py:83::test_export_refuses_existing_destination_and_run_overlap` | export refuses existing destination and run overlap | R |
| `tests/integration/publication/test_exact_markdown.py:94::test_invalidated_review_does_not_export_old_approval` | invalidated review does not export old approval | R |
| `tests/integration/publication/test_exact_markdown.py:104::test_review_shows_current_plan_explanations_outside_exact_body` | review shows current plan explanations outside exact body | R |
| `tests/integration/review/test_api_hooks.py:14::test_hold_at_review_stops_before_publish` | hold at review stops before publish | R |
| `tests/integration/review/test_api_hooks.py:21::test_held_run_stays_held_on_repeated_advance` | held run stays held on repeated advance | R |
| `tests/integration/review/test_api_hooks.py:29::test_approve_then_advance_publishes` | approve then advance publishes | R |
| `tests/integration/review/test_api_hooks.py:38::test_without_hold_the_cli_flow_is_unchanged` | without hold the cli flow is unchanged | R |
| `tests/integration/review/test_api_hooks.py:45::test_resume_never_publishes_an_unapproved_held_run` | resume never publishes an unapproved held run | R |
| `tests/integration/review/test_api_hooks.py:54::test_review_roles_use_review_client` | review roles use review client | R |
| `tests/integration/review/test_api_hooks.py:61::test_agent_started_event_names_the_role` | agent started event names the role | R |
| `tests/integration/review/test_api_hooks.py:69::test_interrupt_request_stops_before_next_agent` | interrupt request stops before next agent | R |
| `tests/integration/review/test_api_hooks.py:80::test_interrupt_can_be_cleared` | interrupt can be cleared | R |
| `tests/integration/review/test_api_hooks.py:89::test_cancelled_run_does_not_advance` | cancelled run does not advance | R |
| `tests/integration/review/test_markdown_workflow.py:20::test_independent_reviews_fresh_check_and_resume` | independent reviews fresh check and resume | R |
| `tests/integration/review/test_markdown_workflow.py:45::test_stale_and_incomplete_approval_rejected` | stale and incomplete approval rejected | R |
| `tests/integration/review/test_markdown_workflow.py:84::test_material_objections_reach_revision_and_fresh_check` | material objections reach revision and fresh check | R |
| `tests/integration/review/test_markdown_workflow.py:106::test_material_final_gap_exhausted_allowance_is_incomplete` | material final gap exhausted allowance is incomplete | R |
| `tests/integration/review/test_markdown_workflow.py:118::test_missing_core_answer_bound_and_preserved` | missing core answer bound and preserved | R |
| `tests/integration/review/test_markdown_workflow.py:144::test_clarification_preserves_plan_and_does_not_confirm_intent` | clarification preserves plan and does not confirm intent | R |
| `tests/integration/review/test_markdown_workflow.py:165::test_illegal_judge_choice_cannot_bypass_authority` | illegal judge choice cannot bypass authority | R |
| `tests/integration/review/test_markdown_workflow.py:177::test_final_checker_allowance_reserved_before_revision` | final checker allowance reserved before revision | R |
| `tests/integration/review/test_markdown_workflow.py:187::test_stale_document_cannot_be_approved` | stale document cannot be approved | R |
| `tests/integration/review/test_markdown_workflow.py:195::test_unknown_provider_usage_remains_blocked` | unknown provider usage remains blocked | R |
| `tests/integration/review/test_markdown_workflow.py:210::test_request_changes_revises_and_rechecks` | request changes revises and rechecks | R |
| `tests/integration/review/test_markdown_workflow.py:232::test_approval_refreshes_publication_metadata` | approval refreshes publication metadata | R |
| `tests/integration/review/test_markdown_workflow.py:242::test_reframe_allowance_preserves_history_and_bounds_loop` | reframe allowance preserves history and bounds loop | R |
| `tests/integration/review/test_markdown_workflow.py:272::test_fabricated_evidence_ref_rejected_at_submit` | fabricated evidence ref rejected at submit | R |
| `tests/integration/review/test_markdown_workflow.py:289::test_markdown_with_internal_evidence_ref_is_rejected` | markdown with internal evidence ref is rejected | R |
| `tests/integration/review/test_markdown_workflow.py:300::test_task_budget_cumulative_after_interrupted_submission` | task budget cumulative after interrupted submission | R |
| `tests/integration/review/test_markdown_workflow.py:314::test_manifest_survives_crash_before_checkpoint_without_provider_replay` | manifest survives crash before checkpoint without provider replay | R |
| `tests/integration/review/test_markdown_workflow.py:347::test_retry_unknown_usage_fails_closed_without_output_allowance` | retry unknown usage fails closed without output allowance | R |
| `tests/integration/review/test_markdown_workflow.py:377::test_retry_success_charges_failed_attempt_its_ceiling_and_continues` | retry success charges failed attempt its ceiling and continues | R |
| `tests/integration/review/test_markdown_workflow.py:404::test_calculation_cannot_claim_fabricated_source_value` | calculation cannot claim fabricated source value | R |
| `tests/integration/review/test_markdown_workflow.py:443::test_stale_revision_cannot_approve_current_complete_plan` | stale revision cannot approve current complete plan | R |
| `tests/integration/review/test_markdown_workflow.py:453::test_start_pins_configured_run_token_ceiling` | start pins configured run token ceiling | R |
| `tests/integration/review/test_markdown_workflow.py:469::test_final_check_revision_transmits_original_final_objections` | final check revision transmits original final objections | R |
| `tests/integration/review/test_markdown_workflow.py:482::test_requested_changes_critics_receive_previously_presented_revision` | requested changes critics receive previously presented revision | R |
| `tests/integration/review/test_markdown_workflow.py:506::test_judge_invalid_usage_never_decreases_run_usage` | judge invalid usage never decreases run usage | R |
| `tests/integration/review/test_markdown_workflow.py:519::test_unknown_judge_usage_is_disclosed_and_call_bounded` | unknown judge usage is disclosed and call bounded | R |
| `tests/integration/review/test_markdown_workflow.py:569::test_resume_after_failed_step_keeps_requested_change_plan` | resume after failed step keeps requested change plan | R |
| `tests/integration/review/test_markdown_workflow.py:599::test_resume_after_failed_revision_keeps_plan_and_objections` | resume after failed revision keeps plan and objections | R |
| `tests/integration/review/test_markdown_workflow.py:623::test_failed_clarification_keeps_review_and_resumes_explanation` | failed clarification keeps review and resumes explanation | R |
| `tests/integration/review/test_markdown_workflow.py:641::test_each_clarification_has_own_allowance_and_record` | each clarification has own allowance and record | R |
| `tests/integration/review/test_markdown_workflow.py:673::test_critic_never_sees_peer_calculation_from_same_round` | critic never sees peer calculation from same round | R |
| `tests/integration/review/test_markdown_workflow.py:704::test_reframe_objections_reach_framing_and_survive_crash` | reframe objections reach framing and survive crash | R |
| `tests/integration/review/test_markdown_workflow.py:729::test_final_check_reframe_passes_all_objections_to_framing` | final check reframe passes all objections to framing | R |
| `tests/integration/review/test_markdown_workflow.py:742::test_run_limits_are_required_and_config_read_as_utf8` | run limits are required and config read as utf8 | R |
| `tests/integration/review/test_markdown_workflow.py:758::test_reject_is_terminal` | reject is terminal | R |
| `tests/integration/review/test_markdown_workflow.py:768::test_source_span_must_be_substantial_exact_text` | source span must be substantial exact text | R |
| `tests/integration/review/test_markdown_workflow.py:780::test_workspace_document_quote_is_a_citable_location_not_evidence` | workspace document quote is a citable location not evidence | R |
| `tests/integration/review/test_markdown_workflow.py:800::test_one_call_reads_several_documents_and_quotes_several_spans` | one call reads several documents and quotes several spans | R |
| `tests/integration/review/test_markdown_workflow.py:829::test_calculation_value_cannot_come_from_a_workspace_document` | calculation value cannot come from a workspace document | R |
| `tests/integration/review/test_markdown_workflow.py:852::test_calculation_source_span_must_be_substantial` | calculation source span must be substantial | R |
| `tests/integration/review/test_markdown_workflow.py:882::test_provider_attempt_event_stores_digest_not_messages` | provider attempt event stores digest not messages | R |
| `tests/integration/review/test_mlse_workflow.py:39::test_index_deterministic_checks_and_publication_preserve_reviewed_bytes` | index deterministic checks and publication preserve reviewed bytes | R |
| `tests/integration/review/test_mlse_workflow.py:59::test_index_mismatch_cannot_be_dropped_by_final_checker` | index mismatch cannot be dropped by final checker | R |
| `tests/integration/review/test_multi_hypothesis_prompt.py:10::test_framing_and_design_ask_for_one_plan_over_every_hypothesis` | framing and design ask for one plan over every hypothesis | R |
| `tests/integration/review/test_resources_gate.py:49::test_resources_gate_stops_before_the_designer_and_approval_continues` | resources gate stops before the designer and approval continues | R |
| `tests/integration/review/test_resources_gate.py:75::test_request_changes_reruns_resources_and_reopens_the_gate` | request changes reruns resources and reopens the gate | R |
| `tests/integration/review/test_resources_gate.py:95::test_unprofiled_runs_skip_the_resources_step` | unprofiled runs skip the resources step | R |
| `tests/integration/review/test_resources_gate.py:105::test_light_mode_approves_the_resources_gate_for_the_system` | light mode approves the resources gate for the system | R |
| `tests/integration/review/test_resources_gate.py:123::test_publish_delivers_the_matching_experiment_index_after_the_plan` | publish delivers the matching experiment index after the plan | R |
| `tests/integration/review/test_review_fixes.py:48::test_changed_run_limits_are_journalled_and_persisted_once` | changed run limits are journalled and persisted once | R |
| `tests/integration/review/test_review_fixes.py:61::test_request_changes_reads_the_persisted_limits` | request changes reads the persisted limits | R |
| `tests/integration/review/test_review_fixes.py:84::test_stop_forced_by_budget_is_decided_again_after_a_ceiling_raise` | stop forced by budget is decided again after a ceiling raise | R |
| `tests/integration/review/test_review_fixes.py:101::test_stop_chosen_by_jev_among_legal_options_stays_final` | stop chosen by jev among legal options stays final | R |
| `tests/integration/review/test_review_fixes.py:119::test_whitespace_question_is_rejected` | whitespace question is rejected | R |
| `tests/integration/review/test_review_fixes.py:125::test_no_route_offers_ask_for_a_whitespace_question` | no route offers ask for a whitespace question | R |
| `tests/integration/review/test_review_fixes.py:149::test_revision_and_final_check_require_hypothesis_and_brief` | revision and final check require hypothesis and brief | R |
| `tests/integration/review/test_review_fixes.py:160::test_revision_findings_are_rendered_into_the_response` | revision findings are rendered into the response | R |
| `tests/integration/review/test_review_fixes.py:176::test_revision_question_goes_to_the_researcher_before_any_rejoinder` | revision question goes to the researcher before any rejoinder | R |
| `tests/integration/review/test_review_fixes.py:216::test_answer_to_a_critic_question_resumes_at_revision` | answer to a critic question resumes at revision | R |
| `tests/integration/review/test_review_fixes.py:238::test_answer_to_a_final_check_question_revises_with_its_objections` | answer to a final check question revises with its objections | R |
| `tests/integration/review/test_review_fixes.py:273::test_reply_without_tool_calls_gets_a_plain_message_and_a_nudge` | reply without tool calls gets a plain message and a nudge | R |
| `tests/integration/review/test_review_fixes.py:283::test_non_object_tool_arguments_are_an_error_result` | non object tool arguments are an error result | R |
| `tests/integration/review/test_review_fixes.py:291::test_source_tool_asks_for_a_verbatim_quote_not_line_numbers` | source tool asks for a verbatim quote not line numbers | R |
| `tests/integration/review/test_review_fixes.py:306::test_read_event_keeps_a_digest_while_the_model_gets_the_text` | read event keeps a digest while the model gets the text | R |
| `tests/integration/review/test_review_fixes.py:328::test_option_enumerators_are_stripped` | option enumerators are stripped | R |
| `tests/integration/review/test_review_fixes.py:335::test_option_text_that_only_looks_numeric_is_kept` | option text that only looks numeric is kept | R |
| `tests/integration/review/test_review_fixes.py:340::test_numbered_options_are_stored_and_shown_unnumbered` | numbered options are stored and shown unnumbered | R |
| `tests/integration/review/test_review_fixes.py:356::test_clarify_keeps_a_recorded_approval` | clarify keeps a recorded approval | R |
| `tests/integration/review/test_review_fixes.py:367::test_review_header_discloses_gaps_and_notes_of_an_incomplete_draft` | review header discloses gaps and notes of an incomplete draft | R |
| `tests/integration/review/test_review_fixes.py:387::test_review_header_of_a_complete_plan_lists_no_gaps` | review header of a complete plan lists no gaps | R |
| `tests/integration/review/test_review_fixes.py:396::test_rejoinder_never_sees_the_peers_accepted_calculation` | rejoinder never sees the peers accepted calculation | R |
| `tests/integration/review/test_review_fixes.py:422::test_reviser_sees_only_persistent_finding_ids` | reviser sees only persistent finding ids | R |
| `tests/integration/review/test_secondary_review.py:9::test_secondary_decision_keeps_stage_attempt_and_exact_comment` | secondary decision keeps stage attempt and exact comment | R |
| `tests/integration/review/test_secondary_review.py:40::test_api_secondary_gate_stays_in_drafting_and_references_uploaded_artifact` | api secondary gate stays in drafting and references uploaded artifact | R |
| `tests/integration/review/test_workspace_agent.py:60::test_first_message_is_an_index_without_document_content` | first message is an index without document content | R |
| `tests/integration/review/test_workspace_agent.py:73::test_unknown_required_document_is_a_workflow_error` | unknown required document is a workflow error | R |
| `tests/integration/review/test_workspace_agent.py:78::test_required_documents_come_in_the_first_message_and_need_no_read` | required documents come in the first message and need no read | R |
| `tests/integration/review/test_workspace_agent.py:90::test_read_and_search_results` | read and search results | R |
| `tests/integration/review/test_workspace_agent.py:124::test_no_check_tool_and_calculate_only_for_computing_roles` | no check tool and calculate only for computing roles | R |
| `tests/integration/review/test_workspace_agent.py:135::test_calculate_from_a_role_without_it_gets_the_unknown_tool_reply` | calculate from a role without it gets the unknown tool reply | R |
| `tests/integration/review/test_workspace_agent.py:142::test_review_role_succeeds_on_the_third_submit` | review role succeeds on the third submit | R |
| `tests/integration/review/test_workspace_agent.py:157::test_rejoinders_see_own_review_and_designer_response_never_the_peer` | rejoinders see own review and designer response never the peer | R |
| `tests/integration/review/test_workspace_agent.py:184::test_maintained_major_from_a_rejoinder_blocks_presentation` | maintained major from a rejoinder blocks presentation | R |
| `tests/integration/review/test_workspace_agent.py:198::test_resume_does_not_rerun_an_accepted_rejoinder` | resume does not rerun an accepted rejoinder | R |
| `tests/integration/review/test_workspace_agent.py:222::test_refinement_reserve_covers_revision_two_rejoinders_and_final_check` | refinement reserve covers revision two rejoinders and final check | R |
| `tests/integration/review/test_workspace_agent.py:245::test_request_changes_checks_the_same_reserve` | request changes checks the same reserve | R |
| `tests/integration/review/test_workspace_agent.py:266::test_valid_options` | valid options | R |
| `tests/integration/review/test_workspace_agent.py:281::test_invalid_options` | invalid options | R |
| `tests/integration/review/test_workspace_agent.py:299::test_option_answer_records_exact_option_text` | option answer records exact option text | R |
| `tests/integration/review/test_workspace_agent.py:311::test_option_with_free_text_keeps_both` | option with free text keeps both | R |
| `tests/integration/review/test_workspace_agent.py:319::test_option_out_of_range_is_rejected` | option out of range is rejected | R |
| `tests/integration/review/test_workspace_agent.py:327::test_option_rejected_when_no_options_are_stored` | option rejected when no options are stored | R |
| `tests/integration/review/test_workspace_agent.py:337::test_option_is_only_for_answers` | option is only for answers | R |
| `tests/integration/review/test_workspace_agent.py:344::test_several_questions_drop_options` | several questions drop options | R |
| `tests/integration/review/test_workspace_agent.py:356::test_unknown_refs_get_one_correction_then_are_dropped_without_failing` | unknown refs get one correction then are dropped without failing | R |
| `tests/unit/agents/test_contract_prompts.py:12::test_question_without_choices_retries_twice_then_stops` | question without choices retries twice then stops | R |
| `tests/unit/agents/test_contract_prompts.py:22::test_question_preserves_explicit_default` | question preserves explicit default | R |
| `tests/unit/agents/test_contract_prompts.py:38::test_missing_revision_response_is_honest_after_two_retries` | missing revision response is honest after two retries | R |
| `tests/unit/agents/test_contract_prompts.py:60::test_revision_rebuttal_requires_grounds` | revision rebuttal requires grounds | R |
| `tests/unit/agents/test_contract_prompts.py:72::test_wire_revision_mapping_preserves_rebuttal` | wire revision mapping preserves rebuttal | R |
| `tests/unit/agents/test_contract_prompts.py:96::test_revision_feedback_names_unknown_and_repeated_finding_ids` | revision feedback names unknown and repeated finding ids | R |
| `tests/unit/agents/test_prompts_pack.py:9::test_pack_addenda_and_review_rubric_preserve_legacy_prompt` | pack addenda and review rubric preserve legacy prompt | R |
| `tests/unit/agents/test_prompts_pack.py:21::test_finding_category_is_validated_and_old_output_still_parses` | finding category is validated and old output still parses | R |
| `tests/unit/api/test_activity.py:48::test_projected_activities_are_declared_and_bounded` | projected activities are declared and bounded | R |
| `tests/unit/api/test_activity.py:62::test_pack_activity_forwarding_and_unknown_activity_rejected` | pack activity forwarding and unknown activity rejected | R |
| `tests/unit/api/test_dependencies.py:7::test_vendored_contract_is_pinned_to_one_management_commit` | vendored contract is pinned to one management commit | R |
| `tests/unit/api/test_dependencies.py:14::test_api_dependencies_available` | api dependencies available | R |
| `tests/unit/api/test_findings.py:21::test_severity_anchor_and_category_mapping` | severity anchor and category mapping | R |
| `tests/unit/api/test_findings.py:36::test_current_final_findings_and_stable_engine_ids` | current final findings and stable engine ids | R |
| `tests/unit/api/test_findings.py:66::test_reviewer_schema_failure_opens_a_gate_with_error` | reviewer schema failure opens a gate with error | R |
| `tests/unit/api/test_mapping.py:27::test_phase_of` | phase of | R |
| `tests/unit/api/test_mapping.py:31::test_step_keys_cover_seven_stages` | step keys cover seven stages | R |
| `tests/unit/api/test_mapping.py:37::test_failure_codes` | failure codes | R |
| `tests/unit/api/test_observability.py:9::test_logged_secret_is_masked_on_the_handler_output` | logged secret is masked on the handler output | R |
| `tests/unit/api/test_observability.py:25::test_empty_secret_is_ignored` | empty secret is ignored | R |
| `tests/unit/api/test_projector.py:18::test_start_announces_phase_and_first_step` | start announces phase and first step | R |
| `tests/unit/api/test_projector.py:28::test_role_drives_review_phase` | role drives review phase | R |
| `tests/unit/api/test_projector.py:38::test_step_change_event` | step change event | R |
| `tests/unit/api/test_projector.py:45::test_step_entered_event_changes_step` | step entered event changes step | R |
| `tests/unit/api/test_projector.py:51::test_unmapped_events_emit_nothing` | unmapped events emit nothing | R |
| `tests/unit/api/test_projector.py:58::test_failure_is_announced_once_with_its_code` | failure is announced once with its code | R |
| `tests/unit/api/test_projector.py:67::test_published_status_emits_no_event_here` | published status emits no event here | R |
| `tests/unit/api/test_projector.py:73::test_seq_contiguous` | seq contiguous | R |
| `tests/unit/api/test_projector.py:84::test_sink_failure_surfaces_from_store` | sink failure surfaces from store | R |
| `tests/unit/api/test_projector.py:95::test_open_gate_only_while_waiting_at_stage_six` | open gate only while waiting at stage six | R |
| `tests/unit/api/test_projector.py:104::test_cancelled_run_announces_run_cancelled_only` | cancelled run announces run cancelled only | R |
| `tests/unit/api/test_projector.py:110::test_published_run_never_announces_a_published_phase_change` | published run never announces a published phase change | R |
| `tests/unit/api/test_projector.py:117::test_archive_state_change_reaches_the_projector` | archive state change reaches the projector | R |
| `tests/unit/api/test_projector.py:126::test_coalesced_changes_are_reported_once` | coalesced changes are reported once | R |
| `tests/unit/benchmark/test_hf.py:10::test_metadata_request_auth_and_search` | metadata request auth and search | R |
| `tests/unit/benchmark/test_hf.py:26::test_missing_repository_is_none` | missing repository is none | R |
| `tests/unit/benchmark/test_hf.py:33::test_unavailable_metadata_is_not_an_empty_success` | unavailable metadata is not an empty success | R |
| `tests/unit/benchmark/test_hf.py:40::test_malformed_metadata_is_unavailable` | malformed metadata is unavailable | R |
| `tests/unit/benchmark/test_registry.py:7::test_registry_is_scoped_to_loaded_domain` | registry is scoped to loaded domain | R |
| `tests/unit/benchmark/test_registry.py:22::test_resource_document_preserves_unknown_and_cited_statuses` | resource document preserves unknown and cited statuses | R |
| `tests/unit/benchmark/test_step.py:42::test_full_run_writes_parseable_files_and_four_events` | full run writes parseable files and four events | R |
| `tests/unit/benchmark/test_step.py:62::test_shortfall_is_reported_without_error` | shortfall is reported without error | R |
| `tests/unit/benchmark/test_step.py:67::test_unavailable_hub_leaves_resources_unverified` | unavailable hub leaves resources unverified | R |
| `tests/unit/benchmark/test_step.py:74::test_comment_added_baseline_is_listed_unknown` | comment added baseline is listed unknown | R |
| `tests/unit/benchmark/test_survey_select.py:57::test_researcher_descriptor_becomes_resource_without_download_url` | researcher descriptor becomes resource without download url | R |
| `tests/unit/benchmark/test_survey_select.py:67::test_survey_collects_every_origin_and_quotes_only_visible_urls` | survey collects every origin and quotes only visible urls | R |
| `tests/unit/benchmark/test_survey_select.py:90::test_mention_without_url_is_kept_without_url` | mention without url is kept without url | R |
| `tests/unit/benchmark/test_survey_select.py:106::test_hub_failure_still_returns_registry_and_researcher` | hub failure still returns registry and researcher | R |
| `tests/unit/benchmark/test_survey_select.py:140::test_select_keeps_researcher_dataset_and_ignores_unknown_ids` | select keeps researcher dataset and ignores unknown ids | R |
| `tests/unit/benchmark/test_survey_select.py:177::test_comment_requesting_baseline_adds_unknown_researcher_resource` | comment requesting baseline adds unknown researcher resource | R |
| `tests/unit/benchmark/test_survey_select.py:196::test_additions_are_ignored_without_researcher_comment` | additions are ignored without researcher comment | R |
| `tests/unit/benchmark/test_validate.py:36::test_real_metadata_with_revision_is_verified` | real metadata with revision is verified | R |
| `tests/unit/benchmark/test_validate.py:55::test_metadata_without_revision_is_unknown` | metadata without revision is unknown | R |
| `tests/unit/benchmark/test_validate.py:62::test_missing_repository_is_not_found` | missing repository is not found | R |
| `tests/unit/benchmark/test_validate.py:67::test_unavailable_hub_is_unknown_with_reason` | unavailable hub is unknown with reason | R |
| `tests/unit/benchmark/test_validate.py:73::test_resource_without_hub_repository_is_unknown` | resource without hub repository is unknown | R |
| `tests/unit/benchmark/test_validate.py:79::test_baseline_uses_model_endpoint` | baseline uses model endpoint | R |
| `tests/unit/benchmark/test_validate.py:92::test_baseline_with_source_url_is_cited_not_verified` | baseline with source url is cited not verified | R |
| `tests/unit/benchmark/test_validate.py:99::test_baseline_without_source_stays_unknown` | baseline without source stays unknown | R |
| `tests/unit/benchmark/test_validate.py:104::test_researcher_dataset_needs_sha256_and_version` | researcher dataset needs sha256 and version | R |
| `tests/unit/benchmark/test_validate.py:111::test_preset_verified_status_is_not_trusted` | preset verified status is not trusted | R |
| `tests/unit/benchmark/test_validate.py:121::test_no_non_200_response_is_ever_verified` | no non 200 response is ever verified | R |
| `tests/unit/benchmark/test_validate.py:126::test_minimums_count_verified_and_cited_only_and_never_invent` | minimums count verified and cited only and never invent | R |
| `tests/unit/benchmark/test_validate.py:138::test_minimums_report_missing_benchmark` | minimums report missing benchmark | R |
| `tests/unit/packs/test_content.py:19::test_supported_profiles_keep_common_scientific_rubric` | supported profiles keep common scientific rubric | R |
| `tests/unit/packs/test_content.py:37::test_pack_validators_are_registered` | pack validators are registered | R |
| `tests/unit/packs/test_content.py:43::test_pack_calculators_are_registered` | pack calculators are registered | R |
| `tests/unit/packs/test_examples.py:10::test_hypothesis_examples_have_two_hypotheses` | hypothesis examples have two hypotheses | R |
| `tests/unit/packs/test_examples.py:16::test_hypothesis_examples_have_no_mock_constraints_or_budget` | hypothesis examples have no mock constraints or budget | R |
| `tests/unit/packs/test_examples.py:22::test_removed_grade7_example_is_gone_and_new_examples_exist` | removed grade7 example is gone and new examples exist | R |
| `tests/unit/packs/test_examples.py:30::test_agents_md_names_registry_rule` | agents md names registry rule | R |
| `tests/unit/packs/test_infer.py:13::test_stage_model_infers_type_without_jev_and_records_usage` | stage model infers type without jev and records usage | R |
| `tests/unit/packs/test_infer.py:27::test_confirmed_type_and_legacy_profile_do_not_call_provider` | confirmed type and legacy profile do not call provider | R |
| `tests/unit/packs/test_loader.py:15::test_base_is_always_loaded` | base is always loaded | R |
| `tests/unit/packs/test_loader.py:22::test_unknown_overlay_reports_fallback` | unknown overlay reports fallback | R |
| `tests/unit/packs/test_loader.py:29::test_overlay_merges_and_overrides_in_order` | overlay merges and overrides in order | R |
| `tests/unit/packs/test_loader.py:65::test_invalid_pack_is_workflow_error` | invalid pack is workflow error | R |
| `tests/unit/packs/test_loader.py:71::test_overlay_name_cannot_escape_pack_root` | overlay name cannot escape pack root | R |
| `tests/unit/packs/test_loader.py:76::test_start_run_persists_selected_profile_and_old_runs_default_to_none` | start run persists selected profile and old runs default to none | R |
| `tests/unit/packs/test_validators.py:46::test_validator_rejects_its_defect_and_accepts_valid_design` | validator rejects its defect and accepts valid design | R |
| `tests/unit/packs/test_validators.py:68::test_run_uses_only_pack_enabled_validators` | run uses only pack enabled validators | R |
| `tests/unit/packs/test_validators.py:77::test_duplicate_split_names_are_flagged` | duplicate split names are flagged | R |
| `tests/unit/publication/test_check_index.py:17::test_matching_index_has_no_findings` | matching index has no findings | R |
| `tests/unit/publication/test_check_index.py:22::test_each_mismatch_is_a_material_reproducibility_finding` | each mismatch is a material reproducibility finding | R |
| `tests/unit/publication/test_check_index.py:42::test_unparseable_hypothesis_ids_are_disclosed` | unparseable hypothesis ids are disclosed | R |
| `tests/unit/publication/test_experiment_index.py:31::test_extract_binds_exact_plan_and_counts_tokens` | extract binds exact plan and counts tokens | R |
| `tests/unit/publication/test_experiment_index.py:39::test_invalid_submission_retried_once` | invalid submission retried once | R |
| `tests/unit/publication/test_experiment_index.py:49::test_two_invalid_submissions_stop` | two invalid submissions stop | R |
| `tests/unit/publication/test_experiment_index.py:58::test_exhausted_budget_prevents_call` | exhausted budget prevents call | R |
| `tests/unit/runtime/test_configuration.py:8::test_example_config_loads` | example config loads | R |
| `tests/unit/runtime/test_configuration.py:19::test_load_config_missing_key_env` | load config missing key env | R |
| `tests/unit/runtime/test_configuration.py:35::test_bad_configuration_diagnostics_do_not_expose_values` | bad configuration diagnostics do not expose values | R |
| `tests/unit/runtime/test_configuration.py:53::test_invalid_budgets_rejected` | invalid budgets rejected | R |
| `tests/unit/runtime/test_judge_provider.py:35::test_decide_maps_response` | decide maps response | R |
| `tests/unit/runtime/test_judge_provider.py:45::test_decide_rejects_choice_outside_options` | decide rejects choice outside options | R |
| `tests/unit/runtime/test_judge_provider.py:50::test_decide_retries_then_raises` | decide retries then raises | R |
| `tests/unit/runtime/test_judge_provider.py:57::test_model_passed_only_when_set` | model passed only when set | R |
| `tests/unit/runtime/test_judge_provider.py:65::test_retry_attempts_are_exposed_and_can_be_capped` | retry attempts are exposed and can be capped | R |
| `tests/unit/runtime/test_judge_provider.py:79::test_invalid_jev_usage_cannot_reduce_consumed_tokens` | invalid jev usage cannot reduce consumed tokens | R |
| `tests/unit/runtime/test_judge_provider.py:94::test_missing_jev_usage_remains_disclosed_under_call_budget` | missing jev usage remains disclosed under call budget | R |
| `tests/unit/runtime/test_llm_provider.py:42::test_complete_parses_tool_calls` | complete parses tool calls | R |
| `tests/unit/runtime/test_llm_provider.py:73::test_invalid_cached_token_accounting_is_rejected` | invalid cached token accounting is rejected | R |
| `tests/unit/runtime/test_llm_provider.py:84::test_complete_retries_429_then_succeeds` | complete retries 429 then succeeds | R |
| `tests/unit/runtime/test_llm_provider.py:101::test_complete_raises_after_max_retries` | complete raises after max retries | R |
| `tests/unit/runtime/test_llm_provider.py:119::test_negative_usage_is_rejected` | negative usage is rejected | R |
| `tests/unit/runtime/test_llm_provider.py:130::test_retry_attempt_telemetry_preserves_unknown_usage` | retry attempt telemetry preserves unknown usage | R |
| `tests/unit/runtime/test_llm_provider.py:156::test_missing_usage_is_recorded_and_rejected` | missing usage is recorded and rejected | R |
| `tests/unit/runtime/test_llm_provider.py:169::test_llm_retry_attempts_can_be_capped` | llm retry attempts can be capped | R |
| `tests/unit/runtime/test_llm_provider.py:191::test_rejected_request_usage_is_zero_only_for_4xx_except_408` | rejected request usage is zero only for 4xx except 408 | R |
| `tests/unit/runtime/test_llm_provider.py:208::test_agent_request_requires_a_tool_call` | agent request requires a tool call | R |
| `tests/unit/runtime/test_llm_provider.py:224::test_dropped_connection_is_retried_with_unknown_usage` | dropped connection is retried with unknown usage | R |
| `tests/unit/runtime/test_request_providers.py:29::test_llm_config_from_request_model` | llm config from request model | R |
| `tests/unit/runtime/test_request_providers.py:55::test_llm_config_rejects_unsupported_provider` | llm config rejects unsupported provider | R |
| `tests/unit/runtime/test_request_providers.py:60::test_llm_config_rejects_invalid_overrides` | llm config rejects invalid overrides | R |
| `tests/unit/runtime/test_request_providers.py:68::test_exd_config_carries_review_model_and_profile` | exd config carries review model and profile | R |
| `tests/unit/runtime/test_request_providers.py:74::test_on_call_reports_usage_without_secret` | on call reports usage without secret | R |
| `tests/unit/runtime/test_request_providers.py:84::test_on_call_reports_every_failed_attempt` | on call reports every failed attempt | R |
| `tests/unit/runtime/test_request_providers.py:98::test_on_call_reports_rejected_request_and_malformed_body` | on call reports rejected request and malformed body | R |
| `tests/unit/runtime/test_request_providers.py:118::test_judge_uses_given_key_without_env` | judge uses given key without env | R |
| `tests/unit/runtime/test_request_providers.py:124::test_judge_still_requires_a_key` | judge still requires a key | R |
| `tests/unit/science/test_calculations.py:48::test_preserved_independent_r_reference` | preserved independent r reference | R |
| `tests/unit/science/test_calculations.py:72::test_cluster_integer_search_and_request_identity` | cluster integer search and request identity | R |
| `tests/unit/science/test_calculations.py:109::test_applicability_and_authority_guards` | applicability and authority guards | R |
| `tests/unit/science/test_calculations.py:152::test_unimplemented_calculator_does_not_reject_methodology` | unimplemented calculator does not reject methodology | R |
| `tests/unit/science/test_calculations.py:167::test_independent_units_must_be_declared` | independent units must be declared | R |
| `tests/unit/science/test_evidence_lookup.py:4::test_numbers_do_not_match_digits_inside_other_numbers` | numbers do not match digits inside other numbers | R |
| `tests/unit/science/test_registry.py:15::test_dispatches_enabled_calculator` | dispatches enabled calculator | R |
| `tests/unit/science/test_registry.py:29::test_disabled_calculator_returns_no_computation` | disabled calculator returns no computation | R |
| `tests/unit/science/test_registry.py:46::test_no_pack_keeps_legacy_calculator_enabled` | no pack keeps legacy calculator enabled | R |
| `tests/unit/science/test_registry.py:51::test_no_pack_does_not_enable_overlay_calculators` | no pack does not enable overlay calculators | R |
| `tests/unit/science/test_registry.py:62::test_active_pack_enables_new_calculator` | active pack enables new calculator | R |
| `tests/unit/science/test_registry.py:75::test_new_quantities_cannot_claim_an_invented_source_span` | new quantities cannot claim an invented source span | R |
| `tests/unit/science/test_stats.py:19::test_independent_reference` | independent reference | R |
| `tests/unit/science/test_stats.py:47::test_missing_inputs_are_not_computed` | missing inputs are not computed | R |
| `tests/unit/science/test_stats.py:52::test_reference_generator_identity` | reference generator identity | R |
| `tests/unit/science/test_stats.py:86::test_invalid_provenance_domains_and_units_do_not_compute` | invalid provenance domains and units do not compute | R |
| `tests/unit/science/test_stats.py:91::test_derived_overflow_is_not_a_computed_result` | derived overflow is not a computed result | R |
| `tests/unit/science/test_stats.py:125::test_nonfinite_scipy_output_is_not_computed` | nonfinite scipy output is not computed | R |
| `tests/unit/test_activity.py:7::test_activity_records_payload_and_rejects_unknown_type` | activity records payload and rejects unknown type | R |

## Added feedback safety guards

These 16 new R declarations exercise selector/gate behavior, with 42 expanded cases in the latest focused run. They protect coverage selection and are separate from the 390 original declarations above.

| Location | Failure guarded | Mark |
|---|---|---|
| `tests/tooling/test_affected_tests.py:42::test_relative_import_and_package_initializer_reach_boundary` | relative import and package initializer reach boundary | R |
| `tests/tooling/test_affected_tests.py:48::test_changed_test_includes_importing_tests` | changed test includes importing tests | R |
| `tests/tooling/test_affected_tests.py:53::test_nested_import_and_dotted_patch_target_reach_tests` | nested import and dotted patch target reach tests | R |
| `tests/tooling/test_affected_tests.py:88::test_shared_unknown_and_deleted_paths_run_full` | shared unknown and deleted paths run full | R |
| `tests/tooling/test_affected_tests.py:92::test_known_empty_diff_and_explicit_docs_skip` | known empty diff and explicit docs skip | R |
| `tests/tooling/test_affected_tests.py:98::test_unparseable_graph_runs_full` | unparseable graph runs full | R |
| `tests/tooling/test_affected_tests.py:114::test_staged_change_is_not_hidden_by_unstaged_restore` | staged change is not hidden by unstaged restore | R |
| `tests/tooling/test_affected_tests.py:135::test_real_git_detects_staged_unstaged_untracked_renames_and_deletions` | real git detects staged unstaged untracked renames and deletions | R |
| `tests/tooling/test_affected_tests.py:164::test_missing_git_or_base_falls_back_to_full` | missing git or base falls back to full | R |
| `tests/tooling/test_affected_tests.py:175::test_explicit_empty_target_cannot_be_treated_as_clean_local_diff` | explicit empty target cannot be treated as clean local diff | R |
| `tests/tooling/test_affected_tests.py:198::test_ignored_importable_files_are_changed_but_caches_are_excluded` | ignored importable files are changed but caches are excluded | R |
| `tests/tooling/test_affected_tests.py:231::test_ignored_example_asset_is_changed_and_runs_full` | ignored example asset is changed and runs full | R |
| `tests/tooling/test_affected_tests.py:255::test_invalid_stored_plan_is_rejected` | invalid stored plan is rejected | R |
| `tests/tooling/test_affected_tests.py:269::test_gate_requires_successful_selection` | gate requires successful selection | R |
| `tests/tooling/test_affected_tests.py:279::test_gate_rejects_failed_or_missing_required_test_lane` | gate rejects failed or missing required test lane | R |
| `tests/tooling/test_affected_tests.py:288::test_gate_rejects_missing_jobs` | gate rejects missing jobs | R |
