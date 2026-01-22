-- Hexis schema: triggers.
SET search_path = public, ag_catalog, "$user";
CREATE TRIGGER trg_memory_timestamp
    BEFORE UPDATE ON memories
    FOR EACH ROW
    EXECUTE FUNCTION update_memory_timestamp();
CREATE TRIGGER trg_importance_on_access
    BEFORE UPDATE ON memories
    FOR EACH ROW
    WHEN (NEW.access_count != OLD.access_count)
    EXECUTE FUNCTION update_memory_importance();
CREATE TRIGGER trg_neighborhood_staleness
    AFTER UPDATE OF importance, status ON memories
    FOR EACH ROW
    EXECUTE FUNCTION mark_neighborhoods_stale();
CREATE TRIGGER trg_auto_episode_assignment
    AFTER INSERT ON memories
    FOR EACH ROW
    EXECUTE FUNCTION assign_to_episode();
CREATE TRIGGER trg_auto_worldview_alignment
    AFTER INSERT ON memories
    FOR EACH ROW
    EXECUTE FUNCTION auto_check_worldview_alignment();
CREATE TRIGGER trg_heartbeat_state_update
INSTEAD OF UPDATE ON heartbeat_state
FOR EACH ROW
EXECUTE FUNCTION heartbeat_state_update_trigger();
CREATE TRIGGER trg_maintenance_state_update
INSTEAD OF UPDATE ON maintenance_state
FOR EACH ROW
EXECUTE FUNCTION maintenance_state_update_trigger();
CREATE TRIGGER memories_emotional_context_insert
BEFORE INSERT ON memories
FOR EACH ROW
EXECUTE FUNCTION apply_emotional_context_to_memory();
