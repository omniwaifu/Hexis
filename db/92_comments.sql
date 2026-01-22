-- Hexis schema: comments.
COMMENT ON FUNCTION fast_recall IS 'Primary retrieval function combining vector similarity, precomputed associations, and temporal context. Hot path - optimized for speed.';
COMMENT ON VIEW heartbeat_state IS 'Singleton view tracking current heartbeat state: energy, counts, timestamps.';
COMMENT ON FUNCTION execute_heartbeat_action IS 'Execute a single action, deducting energy and returning results.';
COMMENT ON FUNCTION gather_turn_context IS 'Gather full context for LLM decision: environment, goals, memories, identity, self_model, worldview, narrative, relationships, contradictions, emotional patterns, transformations, energy.';
