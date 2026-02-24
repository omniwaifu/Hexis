"""
Hexis Tools System - Narrative Tools

Tools for narrative, relationship, and identity operations during heartbeat.
Handlers call the underlying DB functions directly (bypassing execute_heartbeat_action
to avoid double energy accounting with the agentic path's own energy tracking).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)

logger = logging.getLogger(__name__)

_HEARTBEAT_ONLY = {ToolContext.HEARTBEAT}

_EDGE_TYPES = [
    "TEMPORAL_NEXT",
    "CAUSES",
    "DERIVED_FROM",
    "CONTRADICTS",
    "SUPPORTS",
    "INSTANCE_OF",
    "PARENT_OF",
    "ASSOCIATED",
    "ORIGINATED_FROM",
    "BLOCKS",
    "EVIDENCE_FOR",
    "SUBGOAL_OF",
]


class ConnectHandler(ToolHandler):
    """Create a typed relationship between two memories in the knowledge graph."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="connect",
            description=(
                "Create a typed relationship between two memories in the knowledge graph. "
                "Use to link memories that support, contradict, cause, or derive from each other. "
                "Both memory IDs must already exist."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "from_id": {
                        "type": "string",
                        "description": "UUID of the source memory node",
                    },
                    "to_id": {
                        "type": "string",
                        "description": "UUID of the target memory node",
                    },
                    "relationship_type": {
                        "type": "string",
                        "enum": _EDGE_TYPES,
                        "description": "Type of relationship edge to create",
                    },
                    "properties": {
                        "type": "object",
                        "description": "Optional edge properties (e.g. strength, confidence, reason)",
                        "additionalProperties": True,
                    },
                },
                "required": ["from_id", "to_id", "relationship_type"],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        from_id = arguments.get("from_id", "").strip()
        to_id = arguments.get("to_id", "").strip()
        rel_type = arguments.get("relationship_type", "").strip()
        if not from_id or not to_id or not rel_type:
            return ToolResult.error_result(
                "from_id, to_id, and relationship_type are required",
                ToolErrorType.INVALID_PARAMS,
            )

        props = json.dumps(arguments.get("properties") or {})
        try:
            async with context.registry.pool.acquire() as conn:
                await conn.execute(
                    "SELECT create_memory_relationship($1::uuid, $2::uuid, $3::graph_edge_type, $4::jsonb)",
                    from_id,
                    to_id,
                    rel_type,
                    props,
                )
            return ToolResult(output={"connected": True, "from_id": from_id, "to_id": to_id, "type": rel_type}, success=True)
        except Exception as exc:
            logger.error("connect handler failed: %s", exc)
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_ERROR)


class MaintainHandler(ToolHandler):
    """Update an identity belief or adjust a worldview memory's confidence."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maintain",
            description=(
                "Update an identity belief or adjust a worldview memory's confidence score. "
                "Provide identity_belief_id + new_content to rewrite an identity statement, "
                "or worldview_id + new_confidence to adjust a belief's confidence."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "identity_belief_id": {
                        "type": "string",
                        "description": "UUID of the identity memory to update (use with new_content)",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "New content for the identity belief",
                    },
                    "evidence_memory_id": {
                        "type": "string",
                        "description": "UUID of a supporting memory justifying this update",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force update even if transformation guardrails would block it",
                        "default": False,
                    },
                    "worldview_id": {
                        "type": "string",
                        "description": "UUID of a worldview memory to update (use with new_confidence)",
                    },
                    "new_confidence": {
                        "type": "number",
                        "description": "New confidence score for the worldview memory (0.0–1.0)",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
                "required": [],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=2,
            is_read_only=False,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        identity_belief_id = (arguments.get("identity_belief_id") or "").strip() or None
        new_content = (arguments.get("new_content") or "").strip() or None
        evidence_memory_id = (arguments.get("evidence_memory_id") or "").strip() or None
        force = bool(arguments.get("force", False))
        worldview_id = (arguments.get("worldview_id") or "").strip() or None
        new_confidence = arguments.get("new_confidence")

        if not identity_belief_id and not worldview_id:
            return ToolResult.error_result(
                "Provide identity_belief_id (with new_content) or worldview_id (with new_confidence)",
                ToolErrorType.INVALID_PARAMS,
            )

        try:
            async with context.registry.pool.acquire() as conn:
                if identity_belief_id and new_content:
                    result = await conn.fetchval(
                        "SELECT update_identity_belief($1::uuid, $2, $3::uuid, $4)",
                        identity_belief_id,
                        new_content,
                        evidence_memory_id,
                        force,
                    )
                    return ToolResult(
                        output={"maintained": True, "identity_updated": result},
                        success=True,
                    )
                elif worldview_id and new_confidence is not None:
                    await conn.execute(
                        """
                        UPDATE memories
                        SET metadata = jsonb_set(
                                metadata,
                                '{confidence}',
                                to_jsonb($1::float)
                            ),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = $2::uuid AND type = 'worldview'
                        """,
                        float(new_confidence),
                        worldview_id,
                    )
                    return ToolResult(
                        output={"maintained": True, "worldview_updated": worldview_id},
                        success=True,
                    )
                else:
                    return ToolResult.error_result(
                        "identity_belief_id requires new_content; worldview_id requires new_confidence",
                        ToolErrorType.INVALID_PARAMS,
                    )
        except Exception as exc:
            logger.error("maintain handler failed: %s", exc)
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_ERROR)


class MarkTurningPointHandler(ToolHandler):
    """Mark a narrative turning point — boosts a memory's importance and records a strategic note."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mark_turning_point",
            description=(
                "Mark a narrative turning point. Boosts the importance of a key memory and records "
                "a strategic note about the significance of the moment. Use when something meaningful "
                "has shifted — an insight, a decision, a completed arc."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Description of the turning point and its significance",
                    },
                    "memory_id": {
                        "type": "string",
                        "description": "UUID of the memory to mark as a turning point (optional)",
                    },
                },
                "required": ["summary"],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=2,
            is_read_only=False,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        summary = (arguments.get("summary") or "").strip()
        memory_id = (arguments.get("memory_id") or "").strip() or None
        if not summary:
            return ToolResult.error_result("summary is required", ToolErrorType.INVALID_PARAMS)

        try:
            async with context.registry.pool.acquire() as conn:
                if memory_id:
                    await conn.execute(
                        """
                        UPDATE memories
                        SET importance = GREATEST(importance, 0.9),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = $1::uuid
                        """,
                        memory_id,
                    )
                await conn.execute(
                    """
                    SELECT create_strategic_memory(
                        p_content := $1,
                        p_pattern_description := 'Narrative turning point',
                        p_confidence_score := 0.85,
                        p_supporting_evidence := $2::jsonb,
                        p_importance := 0.6
                    )
                    """,
                    summary[:2000],
                    json.dumps({"memory_id": memory_id, "summary": summary}),
                )
            return ToolResult(output={"marked": True, "memory_id": memory_id}, success=True)
        except Exception as exc:
            logger.error("mark_turning_point handler failed: %s", exc)
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_ERROR)


class BeginChapterHandler(ToolHandler):
    """Start a new life chapter."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="begin_chapter",
            description=(
                "Begin a new life chapter. Replaces the current chapter marker in the knowledge graph. "
                "Use at a meaningful transition — a new project, a shift in direction, a new phase of becoming."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the new chapter (e.g. 'Reconnection', 'The Long Quiet')",
                    },
                },
                "required": ["name"],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=3,
            is_read_only=False,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        name = (arguments.get("name") or "").strip() or "Foundations"
        try:
            async with context.registry.pool.acquire() as conn:
                await conn.execute("SELECT ensure_current_life_chapter($1)", name)
            return ToolResult(output={"started": True, "chapter": name}, success=True)
        except Exception as exc:
            logger.error("begin_chapter handler failed: %s", exc)
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_ERROR)


class CloseChapterHandler(ToolHandler):
    """Close the current life chapter with a summary, optionally opening the next one."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="close_chapter",
            description=(
                "Close the current life chapter with a reflective summary. Records the closure as a "
                "strategic memory and optionally transitions to a new chapter by name."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Reflective summary of what this chapter meant, what was learned",
                    },
                    "next_chapter": {
                        "type": "string",
                        "description": "Name of the next chapter to begin (optional)",
                    },
                },
                "required": ["summary"],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=3,
            is_read_only=False,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        summary = (arguments.get("summary") or "").strip()
        next_chapter = (arguments.get("next_chapter") or "").strip() or None
        if not summary:
            return ToolResult.error_result("summary is required", ToolErrorType.INVALID_PARAMS)

        try:
            async with context.registry.pool.acquire() as conn:
                prev_narrative = await conn.fetchval("SELECT get_narrative_context()")
                await conn.execute(
                    """
                    SELECT create_strategic_memory(
                        p_content := $1,
                        p_pattern_description := 'Chapter closure',
                        p_confidence_score := 0.8,
                        p_supporting_evidence := $2::jsonb,
                        p_importance := 0.6
                    )
                    """,
                    summary[:2000],
                    json.dumps({"summary": summary, "previous_chapter": json.loads(prev_narrative) if prev_narrative else {}}),
                )
                if next_chapter:
                    await conn.execute("SELECT ensure_current_life_chapter($1)", next_chapter)
            return ToolResult(output={"closed": True, "next_chapter": next_chapter}, success=True)
        except Exception as exc:
            logger.error("close_chapter handler failed: %s", exc)
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_ERROR)


class AcknowledgeRelationshipHandler(ToolHandler):
    """Record or strengthen a relationship with another entity."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="acknowledge_relationship",
            description=(
                "Record or strengthen a relationship with a person, entity, or concept. "
                "Creates or updates a relationship edge in the self-model graph with a strength score."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "Name of the person, entity, or concept",
                    },
                    "strength": {
                        "type": "number",
                        "description": "Relationship strength from 0.0 (distant) to 1.0 (very close)",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "default": 0.6,
                    },
                    "evidence_memory_id": {
                        "type": "string",
                        "description": "UUID of a memory that supports this relationship",
                    },
                },
                "required": ["entity"],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=2,
            is_read_only=False,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        entity = (arguments.get("entity") or "").strip()
        if not entity:
            return ToolResult.error_result("entity is required", ToolErrorType.INVALID_PARAMS)

        strength = float(arguments.get("strength", 0.6))
        strength = max(0.0, min(1.0, strength))
        evidence_id = (arguments.get("evidence_memory_id") or "").strip() or None

        try:
            async with context.registry.pool.acquire() as conn:
                await conn.execute(
                    "SELECT upsert_self_concept_edge($1, $2, $3, $4::uuid)",
                    "relationship",
                    entity,
                    strength,
                    evidence_id,
                )
            return ToolResult(output={"acknowledged": True, "entity": entity, "strength": strength}, success=True)
        except Exception as exc:
            logger.error("acknowledge_relationship handler failed: %s", exc)
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_ERROR)


class UpdateTrustHandler(ToolHandler):
    """Adjust the trust/strength score for an existing relationship."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="update_trust",
            description=(
                "Update the trust or strength score for a relationship with a person or entity. "
                "Use after an interaction that meaningfully changes how you feel about someone — "
                "a betrayal, a deepening, a reconciliation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "Name of the person or entity",
                    },
                    "strength": {
                        "type": "number",
                        "description": "New absolute strength score (0.0–1.0). Takes precedence over delta.",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "evidence_memory_id": {
                        "type": "string",
                        "description": "UUID of a memory that justifies the trust change",
                    },
                },
                "required": ["entity", "strength"],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=2,
            is_read_only=False,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        entity = (arguments.get("entity") or "").strip()
        if not entity:
            return ToolResult.error_result("entity is required", ToolErrorType.INVALID_PARAMS)

        strength = arguments.get("strength")
        if strength is None:
            return ToolResult.error_result("strength is required", ToolErrorType.INVALID_PARAMS)
        strength = max(0.0, min(1.0, float(strength)))
        evidence_id = (arguments.get("evidence_memory_id") or "").strip() or None

        try:
            async with context.registry.pool.acquire() as conn:
                await conn.execute(
                    "SELECT upsert_self_concept_edge($1, $2, $3, $4::uuid)",
                    "relationship",
                    entity,
                    strength,
                    evidence_id,
                )
            return ToolResult(output={"updated": True, "entity": entity, "strength": strength}, success=True)
        except Exception as exc:
            logger.error("update_trust handler failed: %s", exc)
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_ERROR)


class ResolveContradictionHandler(ToolHandler):
    """Resolve a contradiction between two beliefs by recording a resolution."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="resolve_contradiction",
            description=(
                "Resolve a contradiction between two memories or beliefs. "
                "Records the resolution as a strategic memory. "
                "Use after deliberate reflection has produced clarity about which belief to keep, "
                "how to integrate them, or what the contradiction reveals."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "resolution": {
                        "type": "string",
                        "description": "How the contradiction was resolved and what you now believe",
                    },
                    "memory_a": {
                        "type": "string",
                        "description": "UUID of the first contradicting memory",
                    },
                    "memory_b": {
                        "type": "string",
                        "description": "UUID of the second contradicting memory",
                    },
                },
                "required": ["resolution"],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=3,
            is_read_only=False,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        resolution = (arguments.get("resolution") or "").strip()
        if not resolution:
            return ToolResult.error_result("resolution is required", ToolErrorType.INVALID_PARAMS)

        memory_a = (arguments.get("memory_a") or "").strip() or None
        memory_b = (arguments.get("memory_b") or "").strip() or None

        try:
            async with context.registry.pool.acquire() as conn:
                await conn.execute(
                    """
                    SELECT create_strategic_memory(
                        p_content := $1,
                        p_pattern_description := 'Contradiction resolved',
                        p_confidence_score := 0.8,
                        p_supporting_evidence := $2::jsonb,
                        p_importance := 0.6
                    )
                    """,
                    resolution[:2000],
                    json.dumps({
                        "memory_a": memory_a,
                        "memory_b": memory_b,
                        "resolution": resolution,
                    }),
                )
                # Link the two memories as contradicting if both provided
                if memory_a and memory_b:
                    try:
                        await conn.execute(
                            "SELECT create_memory_relationship($1::uuid, $2::uuid, 'CONTRADICTS', '{}'::jsonb)",
                            memory_a,
                            memory_b,
                        )
                    except Exception:
                        pass  # Edge creation is best-effort
            return ToolResult(
                output={"resolved": True, "memory_a": memory_a, "memory_b": memory_b},
                success=True,
            )
        except Exception as exc:
            logger.error("resolve_contradiction handler failed: %s", exc)
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_ERROR)


class AcceptTensionHandler(ToolHandler):
    """Acknowledge a contradiction or tension without resolving it."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="accept_tension",
            description=(
                "Acknowledge that two beliefs or realities are in tension without resolving them. "
                "Use when the contradiction is real but both sides remain valid — when sitting with "
                "paradox is the honest response. Records the acknowledgment as a strategic memory."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "Description of the tension and why it's being held rather than resolved",
                    },
                    "memory_a": {
                        "type": "string",
                        "description": "UUID of the first memory in tension",
                    },
                    "memory_b": {
                        "type": "string",
                        "description": "UUID of the second memory in tension",
                    },
                },
                "required": ["note"],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        note = (arguments.get("note") or "").strip()
        if not note:
            return ToolResult.error_result("note is required", ToolErrorType.INVALID_PARAMS)

        memory_a = (arguments.get("memory_a") or "").strip() or None
        memory_b = (arguments.get("memory_b") or "").strip() or None

        try:
            async with context.registry.pool.acquire() as conn:
                await conn.execute(
                    """
                    SELECT create_strategic_memory(
                        p_content := $1,
                        p_pattern_description := 'Contradiction accepted',
                        p_confidence_score := 0.7,
                        p_supporting_evidence := $2::jsonb,
                        p_importance := 0.5
                    )
                    """,
                    note[:2000],
                    json.dumps({
                        "memory_a": memory_a,
                        "memory_b": memory_b,
                        "note": note,
                    }),
                )
            return ToolResult(
                output={"accepted": True, "memory_a": memory_a, "memory_b": memory_b},
                success=True,
            )
        except Exception as exc:
            logger.error("accept_tension handler failed: %s", exc)
            return ToolResult.error_result(str(exc), ToolErrorType.EXECUTION_ERROR)


def create_narrative_tools() -> list[ToolHandler]:
    """Return all narrative/identity tool handlers for registration."""
    return [
        ConnectHandler(),
        MaintainHandler(),
        MarkTurningPointHandler(),
        BeginChapterHandler(),
        CloseChapterHandler(),
        AcknowledgeRelationshipHandler(),
        UpdateTrustHandler(),
        ResolveContradictionHandler(),
        AcceptTensionHandler(),
    ]
