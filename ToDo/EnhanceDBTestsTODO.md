# Enhance DB Tests

For each function below, add as many test cases as necessary to fully test the function and achieve at least 90% code coverage of its logic.

## Schema Bootstrap & Embedding Dimension

- [x] `embedding_dimension`
- [x] `sync_embedding_dimension_config`

## Deliberate Transformation

- [x] `default_transformation_state`
- [x] `normalize_transformation_state`
- [x] `get_transformation_config`
- [x] `begin_belief_exploration`
- [x] `record_transformation_effort`
- [x] `abandon_belief_exploration`
- [x] `attempt_worldview_transformation`
- [x] `get_transformation_progress`
- [x] `get_active_transformations_context`
- [x] `calibrate_neutral_belief`
- [x] `initialize_personality`
- [x] `initialize_core_values`
- [x] `initialize_worldview`
- [x] `check_transformation_readiness`
- [x] `find_episode_memories_graph`
- [x] `get_memory_neighborhoods`

## Helper Functions

- [x] `age_in_days`
- [x] `calculate_relevance`
- [x] `get_embedding`
- [x] `check_embedding_service_health`

## Core Memory Functions

- [x] `update_memory_timestamp`
- [x] `update_memory_importance`
- [x] `mark_neighborhoods_stale`
- [x] `assign_to_episode`
- [x] `fast_recall`

## Provenance & Trust

- [x] `normalize_source_reference`
- [x] `recall_memories_filtered`
- [x] `touch_memories`
- [x] `get_memory_by_id`
- [x] `get_memories_summary`
- [x] `list_recent_memories`
- [x] `get_episode_details`
- [x] `get_episode_memories`
- [x] `list_recent_episodes`
- [x] `search_clusters_by_query`
- [x] `get_cluster_sample_memories`
- [x] `find_related_concepts_for_memories`
- [x] `search_procedural_memories`
- [x] `search_strategic_memories`
- [x] `normalize_source_references`
- [x] `dedupe_source_references`
- [x] `source_reinforcement_score`
- [x] `compute_worldview_alignment`
- [x] `compute_semantic_trust`
- [x] `sync_memory_trust`
- [x] `add_semantic_source_reference`
- [x] `get_memory_truth_profile`
- [x] `update_worldview_confidence_from_influences`
- [x] `create_memory`
- [x] `create_episodic_memory`
- [x] `create_semantic_memory`
- [x] `create_procedural_memory`
- [x] `create_strategic_memory`
- [x] `create_worldview_memory`
- [x] `create_worldview_belief`
- [x] `update_identity_belief`
- [x] `batch_create_memories`
- [x] `create_memory_with_embedding`
- [x] `batch_create_memories_with_embeddings`
- [x] `search_similar_memories`
- [x] `assign_memory_to_clusters`
- [x] `recalculate_cluster_centroid`
- [x] `create_memory_relationship`
- [x] `auto_check_worldview_alignment`
- [x] `link_memory_to_concept`
- [x] `create_concept`
- [x] `link_concept_parent`
- [x] `touch_working_memory`
- [x] `promote_working_memory_to_episodic`
- [x] `cleanup_working_memory`
- [x] `add_to_working_memory`
- [x] `search_working_memory`
- [x] `cleanup_embedding_cache`

## Graph Helpers

- [x] `link_memory_to_episode_graph`
- [x] `get_cluster_members_graph`

## Heartbeat System

- [x] `update_drives`
- [x] `satisfy_drive`
- [x] `set_config`
- [x] `get_config`
- [x] `get_config_by_prefixes`
- [x] `delete_config_key`
- [x] `get_config_text`
- [x] `get_config_float`
- [x] `get_config_int`
- [x] `get_agent_consent_status`
- [x] `is_agent_configured`
- [x] `is_agent_terminated`
- [x] `is_self_termination_enabled`
- [x] `get_agent_profile_context`
- [x] `ensure_self_node`
- [x] `ensure_goals_root`
- [x] `ensure_current_life_chapter`
- [x] `upsert_self_concept_edge`
- [x] `get_self_model_context`
- [x] `get_relationships_context`
- [x] `get_narrative_context`
- [x] `get_state`
- [x] `set_state`
- [x] `heartbeat_state_update_trigger`
- [x] `maintenance_state_update_trigger`
- [x] `get_init_status`
- [x] `advance_init_stage`
- [x] `is_init_complete`
- [x] `build_external_call`
- [x] `build_outbox_message`
- [x] `build_user_message`
- [x] `get_action_cost`
- [x] `get_current_energy`
- [x] `update_energy`
- [x] `pause_heartbeat`
- [x] `should_run_heartbeat`
- [x] `should_run_maintenance`
- [x] `run_maintenance_if_due`
- [x] `should_run_subconscious_decider`
- [x] `mark_subconscious_decider_run`
- [x] `run_subconscious_maintenance`
- [x] `terminate_agent`
- [x] `apply_termination_confirmation`
- [x] `record_consent_response`

## Goal Functions

- [x] `touch_goal`
- [x] `add_goal_progress`
- [x] `change_goal_priority`
- [x] `create_goal`
- [x] `sync_goal_node`
- [x] `link_goal_subgoal`
- [x] `link_goal_to_memory`
- [x] `find_goal_memories`
- [x] `sync_memory_node`
- [x] `sync_cluster_node`
- [x] `link_cluster_relationship`
- [x] `find_related_clusters`
- [x] `link_memory_to_cluster_graph`
- [x] `sync_episode_node`

## Context Gathering

- [x] `get_environment_snapshot`
- [x] `get_goals_snapshot`
- [x] `get_goals_by_priority`
- [x] `get_recent_context`
- [x] `get_identity_context`
- [x] `get_worldview_context`
- [x] `get_worldview_snapshot`
- [x] `get_emotional_patterns_context`
- [x] `get_subconscious_context`
- [x] `get_subconscious_chat_context`
- [x] `get_chat_context`
- [x] `record_subconscious_exchange`
- [x] `record_chat_turn`
- [x] `get_contradictions_context`

## Initialization Flow

- [x] `get_init_profile`
- [x] `merge_init_profile`
- [x] `init_llm_config`
- [x] `init_mode`
- [x] `init_heartbeat_settings`
- [x] `init_identity`
- [x] `init_personality`
- [x] `init_values`
- [x] `init_worldview`
- [x] `init_boundaries`
- [x] `init_interests`
- [x] `init_goals`
- [x] `init_relationship`
- [x] `request_consent`
- [x] `init_consent`
- [x] `init_with_defaults`
- [x] `run_full_initialization`
- [x] `reset_initialization`

## Core Heartbeat Functions

- [x] `start_heartbeat`
- [x] `run_heartbeat`

## Boundaries

- [x] `check_boundaries`

## Emotional State

- [x] `normalize_affective_state`
- [x] `get_current_affective_state`
- [x] `set_current_affective_state`
- [x] `get_emotional_context_for_memory`
- [x] `regulate_emotional_state`
- [x] `sense_memory_availability`
- [x] `request_background_search`
- [x] `process_background_searches`
- [x] `decay_activation_boosts`
- [x] `cleanup_memory_activations`
- [x] `get_spontaneous_memories`
- [x] `update_mood`
- [x] `learn_emotional_trigger`
- [x] `match_emotional_triggers`
- [x] `initialize_innate_emotions`
- [x] `ensure_emotion_bootstrap`
- [x] `apply_emotional_context_to_memory`
- [x] `gather_turn_context`
- [x] `complete_heartbeat`
- [x] `finalize_heartbeat`

## Neighborhood Recomputation

- [x] `recompute_neighborhood`
- [x] `batch_recompute_neighborhoods`

## Graph Enhancements

- [x] `discover_relationship`
- [x] `link_memory_supports_worldview`
- [x] `find_contradictions`
- [x] `find_causal_chain`
- [x] `find_connected_concepts`
- [x] `find_memories_by_concept`
- [x] `find_supporting_evidence`

## Reflect Pipeline

- [x] `process_reflection_result`

## Subconscious Observations

- [x] `apply_subconscious_observations`
- [x] `apply_brainstormed_goals`
- [x] `apply_inquiry_result`
- [x] `apply_goal_changes`
- [x] `apply_external_call_result`
- [x] `execute_heartbeat_action`
- [x] `execute_heartbeat_actions_batch`
- [x] `apply_heartbeat_decision`

## Tip of Tongue / Partial Activation

- [x] `find_partial_activations`
