# Self-Development Capabilities

Note: The source of truth is `db/*.sql`. This document explains how self-development works with the current architecture.

## What Develops Automatically (Subconscious)

The subconscious decider runs on a maintenance schedule and emits **observations**, not actions. These observations are persisted as strategic memories or graph edges so the conscious layer can see them later.

- Narrative moments
  - Life chapters are updated via `ensure_current_life_chapter()`.
  - Turning points are tagged by raising memory importance and writing strategic memories.
- Relationships
  - Relationship edges are stored as `SelfNode` → `ConceptNode` with `kind='relationship'`.
  - Strength is adjusted based on observed interactions.
- Contradictions
  - `CONTRADICTS` edges are created between memory nodes.
  - Coherence drive is nudged upward to surface the tension.
- Emotional patterns
  - Strategic memories with `supporting_evidence.kind = 'emotional_pattern'` are created.
- Consolidation
  - `ASSOCIATED` edges link related memories.
  - Concepts can be extracted with `link_memory_to_concept()`.

## What Requires Conscious Attention

The conscious layer (heartbeat + MCP tools) is responsible for deliberate choices and outward actions:

- Goal selection and reprioritization
- External outreach (user/public)
- Resolution of contradictions (or explicit acceptance)
- Narrative commitments like explicit chapter closure
- Self-termination decisions

## Structures That Encode Growth

- **Worldview memories**: `memories` with `type='worldview'` and `metadata.category` (`self`, `belief`, `other`).
- **Self-model edges**: `SelfNode` → `ConceptNode` edges with `kind` and `strength`.
- **Narrative context**: `LifeChapterNode` linked from `SelfNode`.
- **Contradictions**: `CONTRADICTS` graph edges between memory nodes.
- **Emotional patterns**: `strategic` memories with `supporting_evidence.kind='emotional_pattern'`.

## Example Flow

1. A heartbeat records an episodic memory.
2. The subconscious decider notices a pattern (e.g., a chapter transition).
3. It records a strategic memory and updates the life chapter.
4. The next heartbeat sees the updated narrative/relationship/contradiction context and can respond deliberately.

## Configuration

- `maintenance.subconscious_enabled` — Toggle subconscious decider
- `maintenance.subconscious_interval_seconds` — Decider cadence
- `llm.subconscious` — Model configuration for subconscious pattern detection
