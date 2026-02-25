"""
Hexis Tools System - Memory Tools

Tools for memory operations (recall, remember, etc.).
These wrap the existing CognitiveMemory API.
"""

from __future__ import annotations

import json
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


class RecallHandler(ToolHandler):
    """Search memories by semantic similarity."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="recall",
            description=(
                "Search memories by semantic similarity and/or structured filters. "
                "Use this to find memories related to a topic, concept, or question. "
                "Supports filtering by source, date range, concept graph, and metadata. "
                "Can be used with just filters (no query) for targeted retrieval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query describing what you want to remember. Optional if using filters.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of memories to return (default: 5, max: 50)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "memory_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["episodic", "semantic", "procedural", "strategic", "worldview", "goal"],
                        },
                        "description": (
                            "Filter by memory types. Omit to search ALL types (recommended for most queries). "
                            "Types: episodic (events/experiences), semantic (facts/knowledge), "
                            "procedural (how-to), strategic (patterns/plans), "
                            "worldview (identity, values, beliefs, boundaries, interests), "
                            "goal (objectives/aspirations)."
                        ),
                    },
                    "min_importance": {
                        "type": "number",
                        "description": "Minimum importance score (0.0-1.0).",
                        "default": 0.0,
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "source_path": {
                        "type": "string",
                        "description": "Filter by source path (partial match). E.g., 'hexis/db' for all DB schema memories.",
                    },
                    "source_kind": {
                        "type": "string",
                        "description": "Filter by source kind. E.g., 'code', 'conversation', 'web', 'document'.",
                    },
                    "created_after": {
                        "type": "string",
                        "description": "Only memories created after this ISO date (e.g. '2025-01-15').",
                    },
                    "created_before": {
                        "type": "string",
                        "description": "Only memories created before this ISO date.",
                    },
                    "concept": {
                        "type": "string",
                        "description": "Find memories linked to this concept in the knowledge graph.",
                    },
                },
                "required": [],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        from core.cognitive_memory_api import MemoryType
        from datetime import datetime, timezone

        query = arguments.get("query")
        limit = min(arguments.get("limit", 5), 50)
        memory_types_raw = arguments.get("memory_types")
        min_importance = arguments.get("min_importance", 0.0)
        source_path = arguments.get("source_path")
        source_kind = arguments.get("source_kind")
        created_after_raw = arguments.get("created_after")
        created_before_raw = arguments.get("created_before")
        concept = arguments.get("concept")

        # Must have at least a query or a filter
        has_filters = any([
            memory_types_raw, source_path, source_kind,
            created_after_raw, created_before_raw, concept,
        ])
        if not query and not has_filters:
            return ToolResult.error_result(
                "Provide at least a query or one filter (memory_types, source_path, source_kind, created_after, created_before, concept).",
                ToolErrorType.INVALID_PARAMS,
            )

        # Parse memory types
        memory_types = None
        if memory_types_raw:
            try:
                memory_types = [MemoryType(t) for t in memory_types_raw]
            except ValueError as e:
                return ToolResult.error_result(
                    f"Invalid memory type: {e}",
                    ToolErrorType.INVALID_PARAMS,
                )

        # Parse date filters
        created_after = None
        created_before = None
        try:
            if created_after_raw:
                created_after = datetime.fromisoformat(created_after_raw).replace(tzinfo=timezone.utc) if "+" not in created_after_raw and "Z" not in created_after_raw else datetime.fromisoformat(created_after_raw.replace("Z", "+00:00"))
            if created_before_raw:
                created_before = datetime.fromisoformat(created_before_raw).replace(tzinfo=timezone.utc) if "+" not in created_before_raw and "Z" not in created_before_raw else datetime.fromisoformat(created_before_raw.replace("Z", "+00:00"))
        except ValueError as e:
            return ToolResult.error_result(
                f"Invalid date format: {e}. Use ISO format (e.g. '2025-01-15').",
                ToolErrorType.INVALID_PARAMS,
            )

        try:
            async with context.registry.pool.acquire() as conn:
                type_filter = [t.value for t in memory_types] if memory_types else None
                use_hybrid = bool(
                    query
                    and not any([memory_types_raw, source_path, source_kind, created_after_raw, created_before_raw, concept])
                    and float(min_importance or 0.0) <= 0.0
                )
                if use_hybrid:
                    rows = await conn.fetch(
                        "SELECT * FROM recall_hybrid($1, $2)",
                        query,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT * FROM recall_memories_structured(
                            $1, $2, $3::memory_type[], $4,
                            $5, $6, $7, $8, $9, $10::jsonb
                        )""",
                        query,
                        limit,
                        type_filter,
                        min_importance,
                        source_path,
                        source_kind,
                        created_after,
                        created_before,
                        concept,
                        None,  # metadata_filter
                    )

                memories = []
                for row in rows:
                    mem = {
                        "memory_id": str(row["memory_id"]),
                        "content": row["content"],
                        "type": str(row["memory_type"]),
                        "score": float(row["score"]) if row["score"] is not None else 0.0,
                        "importance": float(row["importance"]) if row["importance"] is not None else 0.0,
                    }
                    if use_hybrid and row.get("source"):
                        mem["retrieval_source"] = row["source"]
                    if row.get("source_attribution"):
                        sa = row["source_attribution"]
                        if isinstance(sa, str):
                            import json
                            try:
                                sa = json.loads(sa)
                            except Exception:
                                sa = {}
                        if isinstance(sa, dict):
                            if sa.get("kind"):
                                mem["source_kind"] = sa["kind"]
                            if sa.get("label"):
                                mem["source_label"] = sa["label"]
                            if sa.get("path"):
                                mem["source_path"] = sa["path"]
                            if sa.get("ref"):
                                mem["source_ref"] = sa["ref"]
                    memories.append(mem)

                # Touch accessed memories
                if memories:
                    memory_ids = [UUID(m["memory_id"]) for m in memories]
                    await conn.execute(
                        "SELECT touch_memories($1::uuid[])",
                        memory_ids,
                    )

            display_query = query or "(filters only)"
            return ToolResult.success_result(
                output={"memories": memories, "count": len(memories), "query": display_query},
                display_output=f"Found {len(memories)} memories for '{display_query}'",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class RememberHandler(ToolHandler):
    """Store a new memory."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="remember",
            description=(
                "Store a new memory. Use this to save important information, "
                "events, or learnings for future recall."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The content to remember.",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["episodic", "semantic", "procedural", "strategic"],
                        "default": "episodic",
                        "description": "Type of memory to create.",
                    },
                    "importance": {
                        "type": "number",
                        "description": "Importance score (0.0-1.0).",
                        "default": 0.5,
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "concepts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Concepts to link this memory to.",
                    },
                },
                "required": ["content"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        content = arguments["content"]
        memory_type = arguments.get("type", "episodic")
        importance = arguments.get("importance", 0.5)
        concepts = arguments.get("concepts", [])

        try:
            async with context.registry.pool.acquire() as conn:
                # Create the memory
                memory_id = await conn.fetchval(
                    """
                    SELECT create_memory(
                        p_type := $1::memory_type,
                        p_content := $2,
                        p_importance := $3
                    )
                    """,
                    memory_type,
                    content,
                    importance,
                )

                # Link concepts in batch
                if concepts:
                    await conn.executemany(
                        "SELECT link_memory_to_concept($1::uuid, $2)",
                        [(memory_id, c) for c in concepts],
                    )

            return ToolResult.success_result(
                output={"memory_id": str(memory_id), "content": content[:100]},
                display_output=f"Stored memory: {content[:50]}...",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class SenseMemoryAvailabilityHandler(ToolHandler):
    """Quick feeling-of-knowing check before full recall."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="sense_memory_availability",
            description=(
                "Sense whether you likely have relevant memories before doing a full recall. "
                "Use this for a quick feeling-of-knowing check."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Topic to check memory availability for.",
                    },
                },
                "required": ["query"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,  # Free - lightweight check
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        query = arguments["query"]

        try:
            async with context.registry.pool.acquire() as conn:
                result = await conn.fetchrow(
                    "SELECT * FROM sense_memory_availability($1)",
                    query,
                )

                if result:
                    return ToolResult.success_result(
                        output=dict(result),
                        display_output=f"Memory availability: {result.get('activation_strength', 0):.2f}",
                    )
                else:
                    return ToolResult.success_result(
                        output={"has_memories": False, "activation_strength": 0.0},
                        display_output="No strong memory activation",
                    )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ExploreConceptHandler(ToolHandler):
    """Explore memories connected to a concept."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="explore_concept",
            description=(
                "Explore memories connected to a specific concept. Shows how different "
                "memories relate to an idea and what other concepts are connected."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "concept": {
                        "type": "string",
                        "description": "The concept to explore.",
                    },
                    "include_related": {
                        "type": "boolean",
                        "description": "Also return memories linked to related concepts.",
                        "default": True,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum memories to return.",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["concept"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        concept = arguments["concept"]
        include_related = arguments.get("include_related", True)
        limit = arguments.get("limit", 5)

        try:
            async with context.registry.pool.acquire() as conn:
                # Find memories linked to concept
                rows = await conn.fetch(
                    """
                    SELECT memory_id, memory_content, memory_type, memory_importance, link_strength
                    FROM find_memories_by_concept($1, $2)
                    """,
                    concept,
                    limit,
                )

                memories = [
                    {
                        "memory_id": str(row["memory_id"]),
                        "content": row["memory_content"],
                        "type": row["memory_type"],
                        "importance": row["memory_importance"],
                        "concept_strength": row["link_strength"],
                    }
                    for row in rows
                ]

                related_concepts = []
                if include_related and memories:
                    memory_ids = [m["memory_id"] for m in memories]
                    related_rows = await conn.fetch(
                        """
                        SELECT name, shared_memories
                        FROM find_related_concepts_for_memories($1::uuid[], $2, 10)
                        """,
                        memory_ids,
                        concept,
                    )
                    related_concepts = [dict(r) for r in related_rows]

            return ToolResult.success_result(
                output={
                    "concept": concept,
                    "memories": memories,
                    "related_concepts": related_concepts,
                    "count": len(memories),
                },
                display_output=f"Found {len(memories)} memories for concept '{concept}'",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class GetProceduresHandler(ToolHandler):
    """Retrieve procedural memories for a task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_procedures",
            description=(
                "Retrieve procedural memories (how-to knowledge) for a specific task. "
                "Returns step-by-step instructions and prerequisites."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The task you want to know how to do.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum procedures to return.",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["task"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        task = arguments["task"]
        limit = arguments.get("limit", 3)

        try:
            async with context.registry.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM fast_recall($1, $2)
                    WHERE type = 'procedural'
                    """,
                    task,
                    limit * 2,  # Fetch more to filter
                )

                procedures = [
                    {
                        "memory_id": str(row["id"]),
                        "content": row["content"],
                        "similarity": row.get("similarity"),
                    }
                    for row in rows[:limit]
                ]

            return ToolResult.success_result(
                output={"procedures": procedures, "count": len(procedures), "task": task},
                display_output=f"Found {len(procedures)} procedures for '{task}'",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class GetStrategiesHandler(ToolHandler):
    """Retrieve strategic memories for a situation."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_strategies",
            description=(
                "Retrieve strategic memories (patterns, heuristics, lessons learned) "
                "applicable to a situation. These are meta-level insights about what works."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "situation": {
                        "type": "string",
                        "description": "The situation you need strategic guidance for.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum strategies to return.",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["situation"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        situation = arguments["situation"]
        limit = arguments.get("limit", 3)

        try:
            async with context.registry.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM fast_recall($1, $2)
                    WHERE type = 'strategic'
                    """,
                    situation,
                    limit * 2,
                )

                strategies = [
                    {
                        "memory_id": str(row["id"]),
                        "content": row["content"],
                        "similarity": row.get("similarity"),
                    }
                    for row in rows[:limit]
                ]

            return ToolResult.success_result(
                output={"strategies": strategies, "count": len(strategies), "situation": situation},
                display_output=f"Found {len(strategies)} strategies for '{situation}'",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class CreateGoalHandler(ToolHandler):
    """Create a new goal for the agent."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="create_goal",
            description=(
                "Create a new goal for the agent to pursue. Use this for reminders, "
                "TODOs, or longer-term objectives."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short goal title.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional longer description.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["active", "queued", "backburner"],
                        "default": "queued",
                        "description": "Desired priority.",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["curiosity", "user_request", "identity", "derived", "external"],
                        "default": "user_request",
                        "description": "Why this goal exists.",
                    },
                },
                "required": ["title"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        title = arguments["title"]
        description = arguments.get("description")
        priority = arguments.get("priority", "queued")
        source = arguments.get("source", "user_request")

        try:
            async with context.registry.pool.acquire() as conn:
                goal_id = await conn.fetchval(
                    """
                    SELECT create_goal(
                        p_title := $1,
                        p_description := $2,
                        p_priority := $3,
                        p_source := $4
                    )
                    """,
                    title,
                    description,
                    priority,
                    source,
                )

            return ToolResult.success_result(
                output={"goal_id": str(goal_id), "title": title, "priority": priority},
                display_output=f"Created goal: {title}",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ScheduleTaskHandler(ToolHandler):
    """Create a scheduled (cron-like) task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="schedule_task",
            description=(
                "Create a scheduled task (cron-like). Use for recurring reminders or timed actions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short task name."},
                    "description": {"type": "string", "description": "Optional longer description."},
                    "schedule_kind": {
                        "type": "string",
                        "enum": ["once", "interval", "daily", "weekly"],
                        "description": "Schedule type.",
                    },
                    "schedule": {"type": "object", "description": "Schedule details for the selected type."},
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name (e.g., America/Los_Angeles).",
                    },
                    "action_kind": {
                        "type": "string",
                        "enum": ["queue_user_message", "create_goal"],
                        "description": "Action to perform when the schedule fires.",
                    },
                    "action_payload": {"type": "object", "description": "Action payload."},
                    "max_runs": {
                        "type": "integer",
                        "description": "Optional max number of runs before auto-disable.",
                    },
                },
                "required": ["name", "schedule_kind", "schedule", "action_kind", "action_payload"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        import json

        name = arguments["name"]
        schedule_kind = arguments["schedule_kind"]
        schedule = arguments.get("schedule") or {}
        action_kind = arguments["action_kind"]
        action_payload = arguments.get("action_payload") or {}
        timezone = arguments.get("timezone")
        description = arguments.get("description")
        max_runs = arguments.get("max_runs")

        try:
            async with context.registry.pool.acquire() as conn:
                task_id = await conn.fetchval(
                    """
                    SELECT create_scheduled_task(
                        $1,
                        $2,
                        $3::jsonb,
                        $4,
                        $5::jsonb,
                        $6,
                        $7,
                        'active',
                        $8,
                        'agent'
                    )
                    """,
                    name,
                    schedule_kind,
                    json.dumps(schedule),
                    action_kind,
                    json.dumps(action_payload),
                    timezone,
                    description,
                    max_runs,
                )

            return ToolResult.success_result(
                output={"task_id": str(task_id), "name": name},
                display_output=f"Scheduled task: {name}",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ListScheduledTasksHandler(ToolHandler):
    """List scheduled tasks."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_scheduled_tasks",
            description="List scheduled tasks with optional filters.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Optional status filter"},
                    "due_before": {"type": "string", "description": "Optional ISO8601 cutoff"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": [],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        status = arguments.get("status")
        due_before = arguments.get("due_before")
        limit = int(arguments.get("limit", 50))

        try:
            async with context.registry.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM list_scheduled_tasks($1, $2::timestamptz, $3)",
                    status,
                    due_before,
                    limit,
                )
            tasks = [dict(row) for row in rows]
            return ToolResult.success_result(
                output={"tasks": tasks, "count": len(tasks)},
                display_output=f"Found {len(tasks)} scheduled task(s)",
            )
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class UpdateScheduledTaskHandler(ToolHandler):
    """Update a scheduled task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="update_scheduled_task",
            description="Update a scheduled task (schedule/action/status).",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "schedule_kind": {"type": "string"},
                    "schedule": {"type": "object"},
                    "timezone": {"type": "string"},
                    "action_kind": {"type": "string"},
                    "action_payload": {"type": "object"},
                    "status": {"type": "string"},
                    "max_runs": {"type": "integer"},
                },
                "required": ["task_id"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        import json

        task_id = arguments["task_id"]
        try:
            async with context.registry.pool.acquire() as conn:
                updated = await conn.fetchval(
                    """
                    SELECT update_scheduled_task(
                        $1::uuid,
                        $2,
                        $3,
                        $4,
                        $5::jsonb,
                        $6,
                        $7,
                        $8::jsonb,
                        $9,
                        $10
                    )
                    """,
                    task_id,
                    arguments.get("name"),
                    arguments.get("description"),
                    arguments.get("schedule_kind"),
                    json.dumps(arguments.get("schedule")) if arguments.get("schedule") is not None else None,
                    arguments.get("timezone"),
                    arguments.get("action_kind"),
                    json.dumps(arguments.get("action_payload")) if arguments.get("action_payload") is not None else None,
                    arguments.get("status"),
                    arguments.get("max_runs"),
                )

            return ToolResult.success_result(
                output={"task": updated},
                display_output=f"Updated task {task_id}",
            )
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class DeleteScheduledTaskHandler(ToolHandler):
    """Disable or delete a scheduled task."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="delete_scheduled_task",
            description="Disable or delete a scheduled task.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "hard_delete": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["task_id"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        task_id = arguments["task_id"]
        hard_delete = bool(arguments.get("hard_delete", False))
        reason = arguments.get("reason")
        try:
            async with context.registry.pool.acquire() as conn:
                ok = await conn.fetchval(
                    "SELECT delete_scheduled_task($1::uuid, $2::boolean, $3)",
                    task_id,
                    hard_delete,
                    reason,
                )
            return ToolResult.success_result(
                output={"deleted": bool(ok), "task_id": task_id},
                display_output=f"Deleted task {task_id}" if hard_delete else f"Disabled task {task_id}",
            )
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class QueueUserMessageHandler(ToolHandler):
    """Queue a message for the user."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="queue_user_message",
            description="Queue a message for external delivery to the user.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message body for the user.",
                    },
                    "intent": {
                        "type": "string",
                        "description": "Optional intent/category (e.g. 'reminder', 'status', 'question').",
                    },
                },
                "required": ["message"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,  # Free - just queuing
            is_read_only=False,
            allowed_contexts={ToolContext.HEARTBEAT},  # Only for autonomous use
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        import json

        message = arguments["message"]
        intent = arguments.get("intent")

        try:
            async with context.registry.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO external_calls (call_type, input, status)
                    VALUES ('outbox_message', $1::jsonb, 'pending')
                    """,
                    json.dumps({"message": message, "intent": intent}),
                )

            return ToolResult.success_result(
                output={"queued": True, "message": message[:50]},
                display_output=f"Queued message: {message[:50]}...",
            )

        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class ListRecentEpisodesHandler(ToolHandler):
    """List recent episodic memory episodes."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_recent_episodes",
            description=(
                "List recent episodic memory episodes — grouped periods of activity with "
                "summaries and memory counts. Useful for reviewing what has happened recently."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent episodes to return (default 5, max 20).",
                        "default": 5,
                    },
                },
                "required": [],
            },
            category=ToolCategory.MEMORY,
            energy_cost=0,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        limit = min(int(arguments.get("limit", 5)), 20)
        try:
            async with context.registry.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, started_at, ended_at, episode_type, summary, memory_count
                    FROM list_recent_episodes($1::int)
                    """,
                    limit,
                )
            episodes = [
                {
                    "id": str(r["id"]),
                    "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                    "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
                    "episode_type": r["episode_type"],
                    "summary": r["summary"],
                    "memory_count": r["memory_count"],
                }
                for r in rows
            ]
            return ToolResult.success_result(
                output={"episodes": episodes, "count": len(episodes)},
                display_output=f"Found {len(episodes)} recent episodes",
            )
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


class BrainstormGoalsHandler(ToolHandler):
    """Gather goal context for brainstorming — current goals, recent episodes, drive levels."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="brainstorm_goals",
            description=(
                "Gather context for goal brainstorming: returns your current goals, "
                "recent episodic activity, and drive levels. Use the output to reason "
                "about what goals to create or update, then call manage_goals to act."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "max_goals": {
                        "type": "integer",
                        "description": "Maximum existing goals to return (default 10).",
                        "default": 10,
                    },
                },
                "required": [],
            },
            category=ToolCategory.MEMORY,
            allowed_contexts=_HEARTBEAT_ONLY,
            energy_cost=2,
            is_read_only=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        try:
            async with context.registry.pool.acquire() as conn:
                snapshot_raw = await conn.fetchval("SELECT get_goals_snapshot()")
                goals = (
                    snapshot_raw if isinstance(snapshot_raw, dict)
                    else json.loads(snapshot_raw) if snapshot_raw
                    else {"goals": []}
                )

                episode_rows = await conn.fetch(
                    """
                    SELECT id, started_at, episode_type, summary, memory_count
                    FROM list_recent_episodes(5)
                    """,
                )
                episodes = [
                    {
                        "id": str(r["id"]),
                        "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                        "episode_type": r["episode_type"],
                        "summary": r["summary"],
                        "memory_count": r["memory_count"],
                    }
                    for r in episode_rows
                ]

                drive_rows = await conn.fetch(
                    "SELECT name, current_level, urgency_threshold FROM drives ORDER BY current_level DESC"
                )
                drives = [
                    {
                        "name": r["name"],
                        "level": round(r["current_level"], 3),
                        "urgent": r["current_level"] >= r["urgency_threshold"],
                    }
                    for r in drive_rows
                ]

            goal_count = len(goals.get("goals", []))
            urgent_drives = [d["name"] for d in drives if d["urgent"]]
            return ToolResult.success_result(
                output={
                    "current_goals": goals,
                    "recent_episodes": episodes,
                    "drives": drives,
                },
                display_output=(
                    f"{goal_count} current goals; "
                    f"{len(episodes)} recent episodes; "
                    f"urgent drives: {', '.join(urgent_drives) or 'none'}"
                ),
            )
        except Exception as e:
            return ToolResult.error_result(str(e), ToolErrorType.EXECUTION_FAILED)


def create_memory_tools() -> list[ToolHandler]:
    """Create memory tool handlers.

    Note: Goal management is handled by core/tools/goals.py (manage_goals).
    Schedule management is handled by core/tools/cron.py (manage_schedule).
    Those unified tools replace the individual create_goal, schedule_task, etc.
    """
    return [
        RecallHandler(),
        RememberHandler(),
        SenseMemoryAvailabilityHandler(),
        ExploreConceptHandler(),
        GetProceduresHandler(),
        GetStrategiesHandler(),
        QueueUserMessageHandler(),
        ListRecentEpisodesHandler(),
        BrainstormGoalsHandler(),
    ]
