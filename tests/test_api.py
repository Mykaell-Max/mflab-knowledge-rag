from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from mflab_knowledge import api
from mflab_knowledge.generation import (
    GenerationConfig,
    GenerationContextTooLargeError,
)
from mflab_knowledge.repository_config import (
    RepositoryCatalog,
    RepositoryDefinition,
)


class _Embedder:
    profile_id = "test-profile"


class _Generator:
    def __init__(self, answer: str = "Supported by [S1].") -> None:
        self.answer = answer
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {
            "answer": self.answer,
            "model": "local-test-model",
            "finish_reason": "stop",
            "usage": {"total_tokens": 20},
        }


class _SequencedGenerator(_Generator):
    def __init__(
        self,
        answers: list[str],
        *,
        finish_reasons: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.answers = iter(answers)
        self.finish_reasons = iter(finish_reasons or ["stop"] * len(answers))

    def generate(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {
            "answer": next(self.answers),
            "model": "local-test-model",
            "finish_reason": next(self.finish_reasons),
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }


class _ComposingGenerator(_SequencedGenerator):
    def __init__(
        self,
        answers: list[str],
        composed_answer: str,
        *,
        context_failures: int = 0,
    ) -> None:
        super().__init__(answers)
        self.composed_answer = composed_answer
        self.context_failures = context_failures
        self.composition_calls: list[dict[str, object]] = []

    def compose_sections(self, **kwargs: object) -> dict[str, object]:
        self.composition_calls.append(kwargs)
        if len(self.composition_calls) <= self.context_failures:
            raise GenerationContextTooLargeError("context too large")
        return {
            "answer": self.composed_answer,
            "model": "local-test-model",
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            },
        }


class _PlanningGenerator(_Generator):
    def __init__(self) -> None:
        super().__init__("The call flow is established [S1].")
        self.plan_calls: list[dict[str, object]] = []

    def plan_retrieval(self, **kwargs: object) -> str:
        self.plan_calls.append(kwargs)
        return (
            '{"queries":["mesh creation initialization call flow"],'
            '"identifiers":["MeshFactory","initialize"],'
            '"aspects":['
            '{"aspect":"initialization","question_span":"inicializada"}]}'
        )


class _InvestigatingGenerator:
    def __init__(self) -> None:
        self.calls = 0
        self.history: list[dict[str, object]] = []

    def investigate(self, **kwargs: object) -> str:
        self.calls += 1
        self.history.append(kwargs)
        if self.calls == 1:
            return (
                '{"coverage":[{"aspect":"entry point","status":"gap",'
                '"chunk_ids":[]}],"actions":[{"tool":"search_code",'
                '"query":"unobserved guessed helper"}],'
                '"keep_chunk_ids":[],"stop":false}'
            )
        if self.calls == 2:
            return (
                '{"coverage":[{"aspect":"entry point","status":"gap",'
                '"chunk_ids":[]}],"actions":[{"tool":"search_code",'
                '"query":"factory create initialize"}],'
                '"keep_chunk_ids":[],"stop":false}'
            )
        return (
            '{"coverage":[{"aspect":"entry point","status":"covered",'
            '"chunk_ids":["correct"]}],"actions":[],'
            '"keep_chunk_ids":["correct"],"stop":true}'
        )


class _ReplanningGenerator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def investigate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return (
                '{"coverage":[{"aspect":"entry point","status":"covered",'
                '"chunk_ids":["weak"]}],"actions":[],'
                '"keep_chunk_ids":["weak"],"stop":false}'
            )
        return (
            '{"coverage":[{"aspect":"entry point","status":"covered",'
            '"chunk_ids":["weak"]}],"actions":[],'
            '"keep_chunk_ids":["weak"],"stop":true}'
        )


class _InvalidThenStoppingGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def investigate(self, **_kwargs: object) -> str:
        self.calls += 1
        if self.calls == 1:
            return "not-json"
        return (
            '{"coverage":[{"aspect":"entry point","status":"covered",'
            '"chunk_ids":["observed"]}],"actions":[],'
            '"keep_chunk_ids":["observed"],"stop":true}'
        )


class _CallGraphGenerator:
    def __init__(self) -> None:
        self.calls = 0
        self.history: list[dict[str, object]] = []

    def investigate(self, **kwargs: object) -> str:
        self.calls += 1
        self.history.append(kwargs)
        if self.calls == 1:
            return (
                '{"coverage":[{"aspect":"call flow","status":"gap",'
                '"chunk_ids":[]}],"actions":['
                '{"tool":"find_callers","chunk_id":"observed"},'
                '{"tool":"find_callees","chunk_id":"observed"}],'
                '"keep_chunk_ids":["observed"],"stop":false}'
            )
        return (
            '{"coverage":[{"aspect":"call flow","status":"covered",'
            '"chunk_ids":["observed"]}],"actions":[],'
            '"keep_chunk_ids":["observed"],"stop":true}'
        )


class _RetryGenerator(_Generator):
    def generate(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise GenerationContextTooLargeError("context too large")
        return {
            "answer": self.answer,
            "model": "local-test-model",
            "finish_reason": "stop",
            "usage": {"total_tokens": 20},
        }


class _VerifyingGenerator(_Generator):
    def __init__(self, answers: list[str], audits: list[str]) -> None:
        super().__init__()
        self.answers = iter(answers)
        self.audits = iter(audits)
        self.verify_calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {
            "answer": next(self.answers),
            "model": "local-test-model",
            "finish_reason": "stop",
            "usage": {"total_tokens": 20},
        }

    def verify(self, **kwargs: object) -> str:
        self.verify_calls.append(kwargs)
        return next(self.audits)


class _SupportDiscoveringGenerator(_VerifyingGenerator):
    def __init__(
        self,
        answers: list[str],
        audits: list[str],
        discoveries: list[str],
    ) -> None:
        super().__init__(answers, audits)
        self.discoveries = iter(discoveries)
        self.discovery_calls: list[dict[str, object]] = []

    def discover_support(self, **kwargs: object) -> str:
        self.discovery_calls.append(kwargs)
        return next(self.discoveries)


class _CoverageVerifyingGenerator(_VerifyingGenerator):
    def __init__(
        self,
        answers: list[str],
        audits: list[str],
        coverage_audits: list[str],
    ) -> None:
        super().__init__(answers, audits)
        self.coverage_audits = iter(coverage_audits)
        self.coverage_calls: list[dict[str, object]] = []

    def assess_coverage(self, **kwargs: object) -> str:
        self.coverage_calls.append(kwargs)
        return next(self.coverage_audits)


class ApiServiceTests(unittest.TestCase):
    def settings(self) -> api.ApiSettings:
        return api.ApiSettings(
            database_url="postgresql://secret@example/test",
            state_dir=Path("missing-state-for-test"),
            generation_config=Path("missing-generation-for-test.toml"),
        )

    def test_notebook_ranking_context_includes_bounded_planner_vocabulary(
        self,
    ) -> None:
        value = api._notebook_ranking_context(
            "Como a malha inicia?",
            {
                "query_plan": {
                    "queries": ["adaptive mesh initialization"],
                    "identifiers": ["MeshFactory::createMesh"],
                }
            },
        )

        self.assertIn("Como a malha inicia?", value)
        self.assertIn("adaptive mesh initialization", value)
        self.assertIn("MeshFactory::createMesh", value)

    def test_notebook_ranking_prioritizes_structural_edges_for_flow(self) -> None:
        ranked = api._rank_notebook_sources(
            "runtime flow",
            "Explain Target runtime flow",
            {
                "S1": {
                    "path": "src/target/state.cpp",
                    "title": "TargetState",
                    "source_kind": "agent_search_evidence",
                },
                "S2": {
                    "path": "src/target/driver.cpp",
                    "title": "Driver::step",
                    "source_kind": "agent_callers_evidence",
                },
            },
        )

        self.assertEqual(ranked[0][2], "S2")

    def test_recovers_selected_lineage_from_persisted_public_graph(self) -> None:
        edges = api._lineage_edges_from_investigation_graph(
            {
                "nodes": [
                    {
                        "id": "chunk:driver",
                        "chunk_id": "driver",
                        "source_id": "S1",
                    },
                    {
                        "id": "chunk:create",
                        "chunk_id": "create",
                        "source_id": "S2",
                    },
                    {
                        "id": "chunk:neighbor",
                        "chunk_id": "neighbor",
                        "source_id": None,
                    },
                ],
                "edges": [
                    {
                        "source": "chunk:driver",
                        "target": "chunk:create",
                        "kind": "calls",
                        "tool": "find_callees",
                        "directed": True,
                        "evidence": "persisted_structure",
                    },
                    {
                        "source": "chunk:driver",
                        "target": "chunk:neighbor",
                        "kind": "calls",
                        "tool": "find_callees",
                        "directed": True,
                        "evidence": "persisted_structure",
                    },
                    {
                        "source": "chunk:create",
                        "target": "chunk:driver",
                        "kind": "neighbor",
                        "tool": "open_neighborhood",
                        "directed": False,
                        "evidence": "persisted_structure",
                    },
                ],
            },
            target_chunk_ids=["create", "neighbor"],
        )

        self.assertEqual(
            edges,
            [
                {
                    "origin_chunk_id": "driver",
                    "target_chunk_id": "create",
                    "kind": "calls_symbol",
                }
            ],
        )

    def test_section_formatter_replaces_headings_but_keeps_code_directives(
        self,
    ) -> None:
        answer = api._format_section_answer(
            (
                "# Unstable model heading\n\nObserved behavior [S1].\n\n"
                "```cpp\n#include <vector>\nstate.advance();\n```"
            ),
            {
                "aspects": [
                    {"aspect_id": "A1", "aspect": "runtime advancement"}
                ]
            },
            position=1,
        )

        self.assertTrue(answer.startswith("Observed behavior"))
        self.assertNotIn("Unstable model heading", answer)
        self.assertIn("#include <vector>", answer)

    def test_context_packing_limits_source_count_without_spending_full_budget(self) -> None:
        packed, used, truncated = api._pack_context_results(
            [
                {"chunk_id": str(position), "text": "x" * 100}
                for position in range(10)
            ],
            max_context_characters=8000,
        )

        self.assertEqual(len(packed), api.CONTEXT_DIVERSITY_TARGET)
        self.assertEqual(used, 600)
        self.assertTrue(truncated)

    def test_evidence_notebook_groups_provenance_without_incidental_nodes(
        self,
    ) -> None:
        notebook = api._build_evidence_notebook(
            [
                {
                    "aspect_id": "A1",
                    "aspect": "entry",
                    "status": "covered",
                    "chunk_ids": ["entry"],
                },
                {
                    "aspect_id": "A2",
                    "aspect": "advance",
                    "status": "partial",
                    "chunk_ids": ["advance"],
                },
                {
                    "aspect_id": "A3",
                    "aspect": "combined local behavior",
                    "status": "covered",
                    "chunk_ids": ["entry", "advance"],
                },
                {
                    "aspect_id": "A4",
                    "aspect": "missing transition",
                    "status": "gap",
                    "chunk_ids": [],
                },
            ],
            [
                {"source_id": "S1", "chunk_id": "entry"},
                {"source_id": "S2", "chunk_id": "advance"},
                {
                    "source_id": "S3",
                    "chunk_id": "caller",
                    "source_kind": "call_graph_caller",
                },
            ],
            max_sections=2,
        )

        self.assertEqual(notebook["algorithm"], "sectional_evidence_notebook_v12")
        self.assertEqual(notebook["ready_sections"], 2)
        self.assertEqual(notebook["covered_aspects"], 3)
        self.assertEqual(notebook["gap_aspects"], 1)
        sections = notebook["sections"]
        aspect_owners = {
            str(aspect["aspect"]): section["section_id"]
            for section in sections
            for aspect in section["aspects"]
        }
        self.assertEqual(aspect_owners["entry"], "E1")
        self.assertEqual(aspect_owners["advance"], "E2")
        self.assertEqual(aspect_owners["combined local behavior"], "E1")
        self.assertNotIn("missing transition", aspect_owners)
        self.assertNotIn(
            "S3",
            sections[0]["source_ids"] + sections[1]["source_ids"],
        )

    def test_evidence_notebook_splits_shared_facets_from_supporting_context(
        self,
    ) -> None:
        notebook = api._build_evidence_notebook(
            [
                {
                    "aspect_id": "A1",
                    "aspect": "first facet",
                    "status": "partial",
                    "chunk_ids": ["coordinator"],
                },
                {
                    "aspect_id": "A2",
                    "aspect": "second facet",
                    "status": "partial",
                    "chunk_ids": ["coordinator"],
                },
            ],
            [
                {"source_id": "S1", "chunk_id": "coordinator"},
                {"source_id": "S2", "chunk_id": "upstream"},
                {"source_id": "S3", "chunk_id": "downstream"},
            ],
        )

        sections = notebook["sections"]
        self.assertEqual(notebook["ready_sections"], 1)
        self.assertEqual(sections[0]["source_ids"], ["S1"])

    def test_evidence_notebook_separates_delivery_from_content_stages(
        self,
    ) -> None:
        notebook = api._build_evidence_notebook(
            [
                {
                    "aspect_id": "A1",
                    "aspect": "configuration",
                    "status": "covered",
                    "chunk_ids": ["configure"],
                },
                {
                    "aspect_id": "A2",
                    "aspect": "particle advancement",
                    "status": "covered",
                    "chunk_ids": ["advance"],
                },
                {
                    "aspect_id": "A3",
                    "aspect": "mechanism explanation",
                    "status": "covered",
                    "chunk_ids": ["configure", "advance"],
                },
                {
                    "aspect_id": "A4",
                    "aspect": "code excerpts",
                    "status": "covered",
                    "chunk_ids": ["configure", "advance"],
                },
            ],
            [
                {
                    "source_id": "S1",
                    "chunk_id": "configure",
                    "path": "src/target/configure.cpp",
                    "title": "Target::configure",
                },
                {
                    "source_id": "S2",
                    "chunk_id": "advance",
                    "path": "src/target/advance.cpp",
                    "title": "Target::advance",
                },
                {
                    "source_id": "S3",
                    "chunk_id": "incidental",
                    "path": "src/time_step.cpp",
                    "title": "TimeStep::initialize",
                },
            ],
            question="Explain Target configuration and particle advancement",
        )

        sections = notebook["sections"]
        self.assertEqual(len(sections), 2)
        self.assertNotIn(
            "S3",
            [
                source_id
                for section in sections
                for source_id in section["source_ids"]
            ],
        )
        source_occurrences = [
            source_id
            for section in sections
            for source_id in section["source_ids"]
        ]
        self.assertEqual(source_occurrences.count("S1"), 1)
        self.assertEqual(source_occurrences.count("S2"), 1)
        content_owners: dict[str, list[str]] = {}
        delivery_owners: dict[str, list[str]] = {}
        for section in sections:
            for aspect in section["aspects"]:
                target = (
                    delivery_owners
                    if aspect["role"] == "delivery"
                    else content_owners
                )
                target.setdefault(aspect["aspect"], []).append(
                    section["section_id"]
                )
        self.assertEqual(content_owners["configuration"], ["E1"])
        self.assertEqual(content_owners["particle advancement"], ["E2"])
        self.assertEqual(delivery_owners["mechanism explanation"], ["E1", "E2"])
        self.assertEqual(delivery_owners["code excerpts"], ["E1", "E2"])

    def test_evidence_notebook_replaces_a_weak_model_anchor(self) -> None:
        notebook = api._build_evidence_notebook(
            [
                {
                    "aspect_id": "A1",
                    "aspect": "initialization",
                    "status": "covered",
                    "chunk_ids": ["timer"],
                }
            ],
            [
                {
                    "source_id": "S1",
                    "chunk_id": "timer",
                    "path": "src/util/timer.cpp",
                    "title": "Timer::initialize",
                    "text": "void Timer::initialize() { value = 0; }",
                },
                {
                    "source_id": "S2",
                    "chunk_id": "target",
                    "path": "src/target/manager.cpp",
                    "title": "TargetManager::initialize",
                    "text": "void TargetManager::initialize() { target.setup(); }",
                },
            ],
            question="Explain Target initialization",
        )

        self.assertIn("S2", notebook["sections"][0]["source_ids"])
        self.assertNotIn("S1", notebook["sections"][0]["source_ids"])

    def test_evidence_notebook_assigns_sources_by_aspect_not_round_robin(
        self,
    ) -> None:
        notebook = api._build_evidence_notebook(
            [
                {
                    "aspect_id": "A1",
                    "aspect": "configuration",
                    "status": "partial",
                    "chunk_ids": ["target-config"],
                },
                {
                    "aspect_id": "A2",
                    "aspect": "particle advancement",
                    "status": "partial",
                    "chunk_ids": ["target-advance"],
                },
                {
                    "aspect_id": "A3",
                    "aspect": "domain integration",
                    "status": "gap",
                    "chunk_ids": [],
                },
            ],
            [
                {
                    "source_id": "S1",
                    "chunk_id": "target-config",
                    "path": "src/target/configurator.cpp",
                    "title": "TargetConfigurator::configure",
                },
                {
                    "source_id": "S2",
                    "chunk_id": "target-advance",
                    "path": "src/target/particle.cpp",
                    "title": "TargetParticle::advance",
                },
                {
                    "source_id": "S3",
                    "chunk_id": "domain-entry",
                    "path": "src/domain/domain.cpp",
                    "title": "Domain::advance",
                },
                {
                    "source_id": "S4",
                    "chunk_id": "neighbor-config",
                    "path": "src/neighbor/poisson.cpp",
                    "title": "Poisson::configure",
                },
            ],
            question=(
                "Explain Target configuration, particle advancement, "
                "and domain integration"
            ),
        )

        by_aspect = {
            str(aspect["aspect"]): set(section["source_ids"])
            for section in notebook["sections"]
            for aspect in section["aspects"]
        }
        self.assertIn("S1", by_aspect["configuration"])
        self.assertNotIn("S4", by_aspect["configuration"])
        self.assertIn("S2", by_aspect["particle advancement"])
        self.assertIn("S3", by_aspect["domain integration"])
        self.assertNotIn("S4", by_aspect["domain integration"])

    def test_evidence_notebook_keeps_repeated_explicit_question_subject(
        self,
    ) -> None:
        notebook = api._build_evidence_notebook(
            [
                {
                    "aspect_id": "A1",
                    "aspect": "configuration",
                    "status": "partial",
                    "chunk_ids": ["primary"],
                }
            ],
            [
                {
                    "source_id": "S1",
                    "chunk_id": "primary",
                    "path": "src/target/configure.cpp",
                    "title": "Target::configure",
                },
                {
                    "source_id": "S2",
                    "chunk_id": "secondary",
                    "path": "src/target/configuration.cpp",
                    "title": "TargetConfiguration",
                },
                {
                    "source_id": "S3",
                    "chunk_id": "runtime",
                    "path": "src/target/runtime.cpp",
                    "title": "Target::advance",
                },
                {
                    "source_id": "S4",
                    "chunk_id": "neighbor",
                    "path": "src/neighbor/configuration.cpp",
                    "title": "NeighborConfiguration",
                },
            ],
            question="Explain Target configuration",
        )

        selected = set(notebook["sections"][0]["source_ids"])
        self.assertIn("S1", selected)
        self.assertIn("S2", selected)
        self.assertNotIn("S4", selected)

    def test_evidence_notebook_excludes_unrelated_graph_neighbors_for_named_subject(
        self,
    ) -> None:
        notebook = api._build_evidence_notebook(
            [
                {
                    "aspect_id": "A1",
                    "aspect": "initialization",
                    "status": "partial",
                    "chunk_ids": ["subject-setup", "neighbor-setup"],
                },
                {
                    "aspect_id": "A2",
                    "aspect": "runtime advancement",
                    "status": "partial",
                    "chunk_ids": ["direct-child"],
                },
            ],
            [
                {
                    "source_id": "S1",
                    "chunk_id": "subject-setup",
                    "path": "src/particle_engine.cpp",
                    "title": "ParticleEngine::setup",
                },
                {
                    "source_id": "S2",
                    "chunk_id": "direct-child",
                    "path": "src/items.cpp",
                    "title": "Items::move",
                },
                {
                    "source_id": "S3",
                    "chunk_id": "neighbor-setup",
                    "path": "src/grid.cpp",
                    "title": "Grid::initialize",
                },
            ],
            question="Explain ParticleEngine initialization and runtime advancement",
            subject_identifiers=["ParticleEngine"],
            related_chunk_ids=["direct-child"],
        )

        selected = {
            str(source_id)
            for section in notebook["sections"]
            for source_id in section["source_ids"]
        }
        self.assertIn("S1", selected)
        self.assertIn("S2", selected)
        self.assertNotIn("S3", selected)

    def test_evidence_notebook_groups_verified_caller_and_callees_as_flow(self) -> None:
        notebook = api._build_evidence_notebook(
            [
                {
                    "aspect_id": "A1",
                    "aspect": "configuration",
                    "status": "partial",
                    "chunk_ids": ["configure"],
                },
                {
                    "aspect_id": "A2",
                    "aspect": "runtime flow",
                    "status": "partial",
                    "chunk_ids": ["move"],
                },
                {
                    "aspect_id": "A3",
                    "aspect": "item creation",
                    "status": "partial",
                    "chunk_ids": ["create"],
                },
            ],
            [
                {
                    "source_id": "S1",
                    "chunk_id": "configure",
                    "path": "src/engine.cpp",
                    "title": "Engine::configure",
                },
                {
                    "source_id": "S2",
                    "chunk_id": "move",
                    "path": "src/engine.cpp",
                    "title": "Engine::moveItems",
                },
                {
                    "source_id": "S3",
                    "chunk_id": "create",
                    "path": "src/engine.cpp",
                    "title": "Engine::createItems",
                },
                {
                    "source_id": "S4",
                    "chunk_id": "step",
                    "path": "src/engine.cpp",
                    "title": "Engine::step",
                },
            ],
            question="Explain Engine configuration and runtime flow",
            subject_identifiers=["Engine"],
            related_chunk_ids=["move", "create"],
            lineage_edges=[
                {
                    "origin_chunk_id": "step",
                    "target_chunk_id": "create",
                },
                {
                    "origin_chunk_id": "step",
                    "target_chunk_id": "move",
                },
            ],
        )

        flow = next(
            section
            for section in notebook["sections"]
            if section.get("status") == "verified_flow"
        )
        self.assertEqual(flow["source_ids"], ["S4", "S3", "S2"])
        self.assertEqual(
            flow["verified_relations"],
            [
                {
                    "origin_source_id": "S4",
                    "target_source_ids": ["S3", "S2"],
                    "kind": "calls_symbol",
                }
            ],
        )
        configuration = next(
            section
            for section in notebook["sections"]
            if any(
                aspect.get("aspect") == "configuration"
                for aspect in section["aspects"]
            )
        )
        self.assertEqual(configuration["source_ids"], ["S1"])

    def test_section_prompt_uses_verified_execution_spine(self) -> None:
        instructions = api._section_synthesis_instructions(
            "Base instructions.",
            {
                "section_id": "E2",
                "aspects": [
                    {
                        "aspect_id": "A2",
                        "aspect": "runtime flow",
                        "role": "content",
                    }
                ],
                "verified_relations": [
                    {
                        "origin_source_id": "S4",
                        "target_source_ids": ["S3", "S2"],
                        "kind": "calls_symbol",
                    }
                ],
            },
            position=2,
            total=2,
            sources=[],
        )

        self.assertIn("VERIFIED EXECUTION SPINE", instructions)
        self.assertIn('"origin_source_id": "S4"', instructions)
        self.assertIn('"target_source_ids": ["S3", "S2"]', instructions)

    def test_evidence_notebook_assigns_gap_to_matching_authorized_context(
        self,
    ) -> None:
        notebook = api._build_evidence_notebook(
            [
                {
                    "aspect_id": "A1",
                    "aspect": "configuration",
                    "status": "partial",
                    "chunk_ids": ["configuration"],
                },
                {
                    "aspect_id": "A2",
                    "aspect": "domain integration",
                    "status": "gap",
                    "chunk_ids": [],
                },
            ],
            [
                {
                    "source_id": "S1",
                    "chunk_id": "configuration",
                    "path": "src/configuration.cpp",
                },
                {
                    "source_id": "S2",
                    "chunk_id": "integration",
                    "path": "src/domain/integration.cpp",
                },
            ],
        )

        self.assertEqual(notebook["ready_sections"], 2)
        self.assertEqual(notebook["candidate_gap_aspects"], 1)
        self.assertEqual(notebook["gap_aspects"], 1)
        self.assertEqual(notebook["sections"][1]["source_ids"], ["S2"])
        self.assertEqual(
            notebook["sections"][1]["status"],
            "candidate_context",
        )

    def test_section_prompt_limits_code_to_visible_lines_in_truncated_sources(self) -> None:
        instructions = api._section_synthesis_instructions(
            api.CONTEXT_INSTRUCTIONS,
            {
                "section_id": "E1",
                "aspects": [{"aspect_id": "A1", "aspect": "flow"}],
            },
            position=1,
            total=1,
            sources=[{"source_id": "S2", "text_truncated": True}],
        )

        self.assertIn("Text-truncated source IDs: S2", instructions)
        self.assertIn("fully and contiguously visible", instructions)
        self.assertIn("never cross the marker", instructions)

    def test_section_prompt_does_not_turn_delivery_facets_into_topics(self) -> None:
        instructions = api._section_synthesis_instructions(
            api.CONTEXT_INSTRUCTIONS,
            {
                "section_id": "E1",
                "aspects": [
                    {
                        "aspect_id": "A1",
                        "aspect": "particle advancement",
                        "role": "content",
                    },
                    {
                        "aspect_id": "A2",
                        "aspect": "code excerpts",
                        "role": "delivery",
                    },
                ],
            },
            position=1,
            total=1,
        )

        self.assertIn("role=content", instructions)
        self.assertIn("role=delivery", instructions)
        self.assertIn("never create a separate paragraph", instructions)

    def test_context_packing_preserves_distinct_paths_before_repeated_chunks(self) -> None:
        packed, _used, _truncated = api._pack_context_results(
            [
                {"chunk_id": "manager-1", "path": "src/manager.cpp", "text": "a"},
                {"chunk_id": "manager-2", "path": "src/manager.cpp", "text": "b"},
                {"chunk_id": "state", "path": "src/state.cpp", "text": "c"},
            ],
            max_context_characters=3000,
        )

        self.assertEqual(
            [result["chunk_id"] for result in packed],
            ["manager-1", "state", "manager-2"],
        )

    def test_context_packing_reserves_aspect_evidence_before_path_diversity(
        self,
    ) -> None:
        packed, _used, _truncated = api._pack_context_results(
            [
                {"chunk_id": "entry", "path": "src/manager.cpp", "text": "a"},
                {"chunk_id": "runtime", "path": "src/manager.cpp", "text": "b"},
                {"chunk_id": "state", "path": "src/state.cpp", "text": "c"},
                {"chunk_id": "helper", "path": "src/helper.cpp", "text": "d"},
            ],
            max_context_characters=4000,
            reserved_chunk_ids=["runtime", "entry"],
        )

        self.assertEqual(
            [result["chunk_id"] for result in packed[:2]],
            ["runtime", "entry"],
        )

    def test_context_packing_shares_large_source_budget_fairly(self) -> None:
        packed, used, _truncated = api._pack_context_results(
            [
                {"chunk_id": str(position), "text": "x" * 4000}
                for position in range(6)
            ],
            max_context_characters=8000,
        )

        self.assertEqual(used, 8000)
        self.assertLessEqual(
            max(len(result["text"]) for result in packed)
            - min(len(result["text"]) for result in packed),
            1,
        )

    def test_context_packing_preserves_source_entry_and_exit(self) -> None:
        text = "entry point\n" + ("middle line\n" * 200) + "terminal call\n"
        packed, _used, truncated = api._pack_context_results(
            [{"chunk_id": "flow", "text": text}],
            max_context_characters=240,
        )

        excerpt = packed[0]["text"]
        self.assertIn("entry point", excerpt)
        self.assertIn("terminal call", excerpt)
        self.assertIn("trecho intermediário omitido", excerpt)
        self.assertTrue(packed[0]["text_truncated"])
        self.assertTrue(truncated)

    def test_section_evidence_revalidates_full_chunk_within_local_budget(
        self,
    ) -> None:
        service = api.RagApiService(self.settings())
        full_text = "function entry\n" + ("body\n" * 100) + "target call\n"
        with mock.patch.object(
            api,
            "fetch_chunks_by_id",
            return_value=[{"chunk_id": "chunk", "text": full_text}],
        ) as fetch:
            sources, used, hydrated = service._section_evidence(
                [
                    {
                        "source_id": "S1",
                        "chunk_id": "chunk",
                        "project": "Generic Solver",
                        "selected_occurrence": {"branch": "trunk"},
                        "text": "function entry",
                        "text_truncated": True,
                    }
                ],
                allowed_access={"lab"},
                max_context_characters=180,
            )

        self.assertEqual(hydrated, 1)
        self.assertLessEqual(used, 180)
        self.assertIn("function entry", sources[0]["text"])
        self.assertIn("target call", sources[0]["text"])
        self.assertEqual(fetch.call_args.kwargs["project"], "Generic Solver")
        self.assertEqual(fetch.call_args.kwargs["branch"], "trunk")

    def test_agent_context_can_request_a_larger_bounded_source_window(self) -> None:
        packed, used, truncated = api._pack_context_results(
            [
                {
                    "chunk_id": str(position),
                    "path": f"src/unit_{position}.cpp",
                    "text": "x" * 2000,
                }
                for position in range(10)
            ],
            max_context_characters=8000,
            source_limit=api.AGENT_CONTEXT_DIVERSITY_TARGET,
        )

        self.assertEqual(len(packed), api.AGENT_CONTEXT_DIVERSITY_TARGET)
        self.assertEqual(used, 8000)
        self.assertTrue(truncated)

    def test_context_packing_keeps_room_for_repeated_lifecycle_methods(self) -> None:
        packed, _used, _truncated = api._pack_context_results(
            [
                {"chunk_id": "manager-init", "path": "src/manager.cpp", "text": "a"},
                {"chunk_id": "manager-run", "path": "src/manager.cpp", "text": "b"},
                {"chunk_id": "domain", "path": "src/domain.cpp", "text": "c"},
                {"chunk_id": "state", "path": "src/state.cpp", "text": "d"},
                {"chunk_id": "factory", "path": "src/factory.cpp", "text": "e"},
                {"chunk_id": "config", "path": "src/config.cpp", "text": "f"},
                {"chunk_id": "test", "path": "tests/test.cpp", "text": "g"},
            ],
            max_context_characters=6000,
        )

        self.assertEqual(
            [result["chunk_id"] for result in packed],
            ["manager-init", "domain", "state", "factory", "config", "manager-run"],
        )

    def test_unit_settings_do_not_implicitly_load_working_directory_catalog(
        self,
    ) -> None:
        with mock.patch.object(api, "load_repository_catalog") as load:
            service = api.RagApiService(self.settings())

        load.assert_not_called()
        self.assertIsNone(service.repository_catalog)
        self.assertIsNone(service.generator)

    def test_resolved_branch_rejects_an_occurrence_from_another_branch(self) -> None:
        result = {
            "project": "Solver",
            "selected_occurrence": {
                "branch": "trunk",
                "commit_sha": "a" * 40,
            },
        }
        scopes = [
            {
                "project": "Solver",
                "branch": "integration",
                "reason": "preferred_default",
            }
        ]

        self.assertFalse(api._matches_resolved_scope(result, scopes))

    def test_health_is_safe_when_database_is_unavailable(self) -> None:
        service = api.RagApiService(self.settings())
        with mock.patch.object(
            api,
            "database_status",
            side_effect=RuntimeError("postgresql://secret@example/test"),
        ):
            result = service.health()

        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("secret", str(result))

    def test_lexical_search_uses_server_side_access_ceiling(self) -> None:
        service = api.RagApiService(self.settings())
        with mock.patch.object(api, "search_postgres", return_value=[]) as search:
            result = service.search(query="DPMManager", mode="lexical")

        self.assertEqual(result["mode"], "lexical")
        self.assertEqual(result["count"], 0)
        self.assertEqual(
            search.call_args.kwargs["allowed_access"], {"public", "lab"}
        )
        with self.assertRaisesRegex(ValueError, "não liberada"):
            service.search(
                query="DPMManager",
                mode="lexical",
                allowed_access={"restricted"},
            )

    def test_hybrid_search_lazily_loads_and_reuses_one_embedder(self) -> None:
        factory = mock.Mock(return_value=_Embedder())
        service = api.RagApiService(
            self.settings(),
            embedder_factory=factory,
        )
        with mock.patch.object(api, "hybrid_search", return_value=[]) as search:
            first = service.search(query="partículas", mode="hybrid")
            second = service.search(query="domínio", mode="hybrid")

        self.assertEqual(first["count"], 0)
        self.assertEqual(second["count"], 0)
        factory.assert_called_once_with()
        self.assertTrue(service.model_loaded)
        self.assertEqual(search.call_count, 2)
        self.assertIs(search.call_args.args[1], factory.return_value)

    def test_project_access_requires_project_filter(self) -> None:
        settings = api.ApiSettings(
            database_url="postgresql:///test",
            allowed_access=frozenset({"public", "project"}),
        )
        service = api.RagApiService(settings)
        with self.assertRaisesRegex(ValueError, "exige o filtro project"):
            service.search(
                query="solver",
                mode="lexical",
                allowed_access={"project"},
            )

    def test_repository_summary_uses_profile_and_service_access(self) -> None:
        service = api.RagApiService(self.settings())
        with mock.patch.object(api, "repository_status", return_value=[]) as status:
            self.assertEqual(service.repositories(), [])

        self.assertEqual(
            status.call_args.kwargs["allowed_access"], {"public", "lab"}
        )
        self.assertTrue(
            str(status.call_args.kwargs["embedding_profile"]).startswith(
                "qwen3-embedding-0.6b-mflab-"
            )
        )

    def test_repository_summary_can_be_narrowed_for_the_web_interface(self) -> None:
        service = api.RagApiService(self.settings())
        with mock.patch.object(api, "repository_status", return_value=[]) as status:
            service.repositories(allowed_access={"public"})

        self.assertEqual(status.call_args.kwargs["allowed_access"], {"public"})
        with self.assertRaisesRegex(ValueError, "não liberada"):
            service.repositories(allowed_access={"restricted"})

    def test_repository_summary_exposes_safe_branch_navigation_policy(self) -> None:
        definition = RepositoryDefinition(
            id="solver",
            enabled=True,
            project="Solver",
            source=Path("source"),
            canonical_ref="origin/trunk",
            branch_scope="remote",
            access_class="lab",
            profile="generic",
            preferred_branch="develop",
            aliases=("solver-next",),
        )
        catalog = RepositoryCatalog(
            path=Path("repositories.toml"),
            config_hash="sha256:test",
            cache_root=Path("cache"),
            inventory_root=Path("inventory"),
            normalized_root=Path("data"),
            repositories=(definition,),
        )
        service = api.RagApiService(
            self.settings(),
            repository_catalog=catalog,
        )
        status = {
            "repository_id": "solver",
            "project": "Solver",
            "branch_names": ["develop", "feature/a", "trunk"],
            "canonical_branches": ["trunk"],
        }
        with mock.patch.object(
            api,
            "repository_status",
            return_value=[status],
        ):
            result = service.repositories()[0]

        self.assertEqual(result["preferred_branch"], "develop")
        self.assertEqual(result["preference_status"], "configured")
        self.assertEqual(result["aliases"], ["solver-next"])
        self.assertEqual(
            result["branch_names"], ["develop", "feature/a", "trunk"]
        )

    def test_repository_summary_matches_stable_database_id_by_unique_project(
        self,
    ) -> None:
        definition = RepositoryDefinition(
            id="solver",
            enabled=True,
            project="Generic Solver",
            source=Path("source"),
            canonical_ref="origin/trunk",
            branch_scope="remote",
            access_class="lab",
            profile="generic",
            preferred_branch="integration",
            aliases=("next",),
        )
        catalog = RepositoryCatalog(
            path=Path("repositories.toml"),
            config_hash="sha256:test",
            cache_root=Path("cache"),
            inventory_root=Path("inventory"),
            normalized_root=Path("data"),
            repositories=(definition,),
        )
        status = {
            "repository_id": "generic-solver-a1b2c3d4e5f6",
            "project": "Generic Solver",
            "branch_names": ["integration", "trunk"],
            "canonical_branches": ["trunk"],
        }
        service = api.RagApiService(
            self.settings(),
            repository_catalog=catalog,
        )

        with mock.patch.object(api, "repository_status", return_value=[status]):
            result = service.repositories()[0]

        self.assertEqual(result["preferred_branch"], "integration")
        self.assertEqual(result["aliases"], ["next"])
        self.assertEqual(result["catalog_repository_id"], "solver")
        self.assertEqual(result["configuration_match"], "unique_project")

    def test_repository_summary_rejects_ambiguous_project_fallback(self) -> None:
        definitions = tuple(
            RepositoryDefinition(
                id=identifier,
                enabled=True,
                project="Shared Project",
                source=Path(identifier),
                canonical_ref="origin/trunk",
                branch_scope="remote",
                access_class="lab",
                profile="generic",
                preferred_branch="integration",
                aliases=(identifier,),
            )
            for identifier in ("solver-a", "solver-b")
        )
        catalog = RepositoryCatalog(
            path=Path("repositories.toml"),
            config_hash="sha256:test",
            cache_root=Path("cache"),
            inventory_root=Path("inventory"),
            normalized_root=Path("data"),
            repositories=definitions,
        )
        service = api.RagApiService(
            self.settings(),
            repository_catalog=catalog,
        )
        status = {
            "repository_id": "shared-project-a1b2c3d4e5f6",
            "project": "Shared Project",
            "branch_names": ["integration", "trunk"],
            "canonical_branches": ["trunk"],
        }

        with mock.patch.object(api, "repository_status", return_value=[status]):
            result = service.repositories()[0]

        self.assertEqual(result["preferred_branch"], "trunk")
        self.assertEqual(result["aliases"], [])
        self.assertEqual(result["configuration_match"], "ambiguous_project")

    def test_automatic_comparison_searches_each_configured_scope(self) -> None:
        definitions = tuple(
            RepositoryDefinition(
                id=identifier,
                enabled=True,
                project=project,
                source=Path(identifier),
                canonical_ref=f"origin/{branch}",
                branch_scope="remote",
                access_class="lab",
                profile="generic",
                preferred_branch=branch,
                aliases=(alias,),
            )
            for identifier, project, branch, alias in (
                ("solver-a", "Solver A", "integration", "modern"),
                ("solver-b", "Solver B", "trunk", "legacy"),
            )
        )
        catalog = RepositoryCatalog(
            path=Path("repositories.toml"),
            config_hash="sha256:test",
            cache_root=Path("cache"),
            inventory_root=Path("inventory"),
            normalized_root=Path("data"),
            repositories=definitions,
        )
        statuses = [
            {
                "repository_id": definition.id,
                "project": definition.project,
                "branch_names": [definition.preferred_branch],
                "canonical_branches": [definition.preferred_branch],
            }
            for definition in definitions
        ]
        service = api.RagApiService(
            self.settings(),
            repository_catalog=catalog,
        )

        def search_backend(_database_url: str, **values: object):
            project = str(values["project"])
            branch = str(values["branch"])
            return [
                {
                    "chunk_id": project,
                    "project": project,
                    "selected_occurrence": {
                        "branch": branch,
                        "commit_sha": "a" * 40,
                    },
                }
            ]

        with mock.patch.object(
            api,
            "repository_status",
            return_value=statuses,
        ):
            with mock.patch.object(
                api,
                "search_postgres",
                side_effect=search_backend,
            ) as search:
                result = service.search(
                    query="Compare modern e legacy",
                    mode="lexical",
                )

        self.assertEqual(search.call_count, 2)
        self.assertEqual(result["scope_resolution"]["mode"], "projects_from_query")
        self.assertEqual(
            [item["project"] for item in result["results"]],
            ["Solver A", "Solver B"],
        )

    def test_non_loopback_host_is_rejected_before_importing_server(self) -> None:
        with self.assertRaisesRegex(ValueError, "exige MFLAB_API_KEY"):
            api.run_api(self.settings(), host="0.0.0.0")

    def test_non_loopback_host_is_allowed_with_a_strong_key(self) -> None:
        settings = api.ApiSettings(
            database_url="postgresql:///test",
            api_key="a" * 48,
        )
        with mock.patch.object(api.importlib, "import_module", side_effect=ImportError):
            with self.assertRaisesRegex(ValueError, "suporte HTTP"):
                api.run_api(settings, host="0.0.0.0")

    def test_bearer_authentication_preserves_local_automation(self) -> None:
        secret = "a" * 48
        self.assertTrue(
            api.api_request_authorized(secret, None, client_host="127.0.0.1")
        )
        self.assertFalse(
            api.api_request_authorized(secret, None, client_host="192.168.1.20")
        )
        self.assertFalse(
            api.api_request_authorized(
                secret,
                "Bearer wrong",
                client_host="192.168.1.20",
            )
        )
        self.assertFalse(
            api.api_request_authorized(
                secret,
                "Bearer chave-inválida",
                client_host="192.168.1.20",
            )
        )
        self.assertTrue(
            api.api_request_authorized(
                secret,
                f"Bearer {secret}",
                client_host="192.168.1.20",
            )
        )

    def test_api_key_is_not_exposed_by_settings_repr(self) -> None:
        secret = "never-print-this-value-12345678901234567890"
        settings = api.ApiSettings(
            database_url="postgresql:///test",
            api_key=secret,
        )
        self.assertNotIn(secret, repr(settings))

    def test_admin_password_is_not_exposed_by_settings_repr(self) -> None:
        secret = "admin-password-never-printed"
        settings = api.ApiSettings(
            database_url="postgresql:///test",
            admin_password=secret,
        )
        self.assertNotIn(secret, repr(settings))

    def test_administration_status_survives_unavailable_backends(self) -> None:
        service = api.RagApiService(
            api.ApiSettings(
                database_url="postgresql:///test",
                admin_password="strong-local-password",
            )
        )
        with mock.patch.object(
            service,
            "health",
            return_value={
                "status": "unavailable",
                "version": "test",
                "database": "unavailable",
            },
        ):
            with mock.patch.object(
                api,
                "embedding_status",
                side_effect=RuntimeError("database unavailable"),
            ):
                with mock.patch.object(
                    service,
                    "repositories",
                    side_effect=RuntimeError("database unavailable"),
                ):
                    with mock.patch.object(api, "read_last_run", return_value=None):
                        with mock.patch.object(
                            api,
                            "_machine_status",
                            return_value={"hostname": "test-host"},
                        ):
                            result = service.administration_status()

        self.assertEqual(result["service"]["status"], "unavailable")
        self.assertEqual(result["database"]["status"], "unavailable")
        self.assertEqual(result["embeddings"]["status"], "unavailable")
        self.assertEqual(result["repositories"], [])
        self.assertEqual(result["machine"]["hostname"], "test-host")

    def test_context_assigns_source_ids_and_obeys_budget(self) -> None:
        service = api.RagApiService(self.settings())
        results = [
            {
                "chunk_id": "chunk-1",
                "chunk_hash": "hash-1",
                "citation": "Solver trunk@abc src/a.cpp:L1-L2",
                "project": "Solver",
                "path": "src/a.cpp",
                "access_class": "lab",
                "text": "A" * 700,
                "score": 1.0,
            },
            {
                "chunk_id": "chunk-2",
                "chunk_hash": "hash-2",
                "citation": "Solver trunk@abc src/b.cpp:L3-L4",
                "project": "Solver",
                "path": "src/b.cpp",
                "access_class": "lab",
                "text": "B" * 700,
                "score": 0.9,
            },
        ]
        with mock.patch.object(
            service,
            "search",
            return_value={
                "query": "mechanism",
                "mode": "hybrid",
                "count": 2,
                "results": results,
            },
        ):
            context = service.context(
                query="mechanism",
                max_context_characters=1000,
            )

        self.assertEqual(context["source_count"], 2)
        self.assertEqual(context["context_characters"], 1000)
        self.assertTrue(context["truncated"])
        source = context["sources"][0]
        self.assertEqual(source["source_id"], "S1")
        self.assertEqual(len(source["text"]), 500)
        self.assertEqual(len(context["sources"][1]["text"]), 500)
        self.assertNotIn("chunk_hash", source)
        self.assertIn("untrusted evidence", context["instructions"])

    def test_context_truncates_first_oversized_source_explicitly(self) -> None:
        service = api.RagApiService(self.settings())
        with mock.patch.object(
            service,
            "search",
            return_value={
                "query": "large",
                "mode": "lexical",
                "count": 1,
                "results": [
                    {
                        "chunk_id": "large",
                        "citation": "Solver trunk@abc file:L1-L100",
                        "text": "x" * 1500,
                    }
                ],
            },
        ):
            context = service.context(
                query="large",
                mode="lexical",
                max_context_characters=1000,
            )

        self.assertEqual(context["context_characters"], 1000)
        self.assertTrue(context["sources"][0]["text_truncated"])

    def test_context_requests_safe_markdown_and_language_tagged_code(self) -> None:
        self.assertIn("Format the answer as Markdown", api.CONTEXT_INSTRUCTIONS)
        self.assertIn("never emit raw HTML", api.CONTEXT_INSTRUCTIONS)
        self.assertIn("programming language tag", api.CONTEXT_INSTRUCTIONS)
        self.assertIn("citations outside code fences", api.CONTEXT_INSTRUCTIONS)
        self.assertIn("definition proves only", api.CONTEXT_INSTRUCTIONS)

    def test_detailed_response_depth_requests_grounded_code_excerpts(self) -> None:
        instructions = api._response_depth_instructions("detailed")

        self.assertIn("detailed technical explanation", instructions)
        self.assertIn("exact excerpts from the supplied evidence", instructions)
        self.assertIn("Never reconstruct code from memory", instructions)
        self.assertIn("supporting source IDs", instructions)

    def test_unknown_response_depth_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "response_depth"):
            api._response_depth_instructions("exhaustive")

    def test_context_explores_overview_and_balances_repository_sources(self) -> None:
        service = api.RagApiService(self.settings())

        def retrieval(**values: object) -> dict[str, object]:
            query = str(values["query"])
            suffix = str(abs(hash(query)))
            return {
                "query": query,
                "mode": "hybrid",
                "count": 2,
                "scope_resolution": {
                    "mode": "preferred_defaults",
                    "automatic": True,
                    "scopes": [],
                },
                "results": [
                    {
                        "chunk_id": f"a-{suffix}",
                        "project": "Solver A",
                        "path": "README.md" if "README" in query else "docs/topic.md",
                        "text": "A overview",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                    },
                    {
                        "chunk_id": f"b-{suffix}",
                        "project": "Solver B",
                        "path": "README.md" if "README" in query else "src/main.cpp",
                        "text": "B overview",
                        "selected_occurrence": {
                            "branch": "trunk",
                            "commit_sha": "b" * 40,
                        },
                    },
                ],
            }

        with mock.patch.object(service, "search", side_effect=retrieval) as search:
            context = service.context(query="O que é o Solver?", limit=6)

        self.assertEqual(search.call_count, 4)
        self.assertEqual(context["exploration"]["intent"], "overview")
        self.assertEqual(
            {source["project"] for source in context["sources"]},
            {"Solver A", "Solver B"},
        )
        self.assertIn("README.md", [source["path"] for source in context["sources"][:2]])
        self.assertIn("Cover every available project", context["instructions"])

    def test_context_uses_auditable_structure_to_guide_overview(self) -> None:
        service = api.RagApiService(self.settings())

        def retrieval(**values: object) -> dict[str, object]:
            return {
                "query": str(values["query"]),
                "mode": "lexical",
                "count": 1,
                "scope_resolution": {
                    "mode": "preferred_defaults",
                    "automatic": True,
                    "scopes": [
                        {"project": "Solver", "branch": "trunk"},
                    ],
                },
                "results": [
                    {
                        "chunk_id": "readme",
                        "project": "Solver",
                        "path": "README.md",
                        "text": "Repository purpose.",
                        "selected_occurrence": {
                            "branch": "trunk",
                            "commit_sha": "a" * 40,
                        },
                    }
                ],
            }

        structure = {
            "schema_version": "0.1",
            "algorithm": "repository_structure_v1",
            "repository_id": "solver-a1",
            "project": "Solver",
            "branch": "trunk",
            "commits": [{"commit_sha": "a" * 40, "documents": 2}],
            "documents": 2,
            "chunks": 4,
            "bytes": 100,
            "formats": [{"format": "cpp", "documents": 2}],
            "top_level": [
                {"name": "src", "kind": "directory", "documents": 2}
            ],
            "access_class": "lab",
            "allowed_access": ["lab"],
            "anchors": [],
            "fingerprint": "sha256:structure",
            "derived_only_from_indexed_metadata": True,
        }
        with mock.patch.object(service, "search", side_effect=retrieval):
            with mock.patch.object(
                api, "repository_structures", return_value=[structure]
            ) as maps:
                context = service.context(
                    query="O que é o Solver?",
                    mode="lexical",
                    limit=4,
                    allowed_access={"lab"},
                )

        maps.assert_called_once()
        self.assertEqual(context["structural_guidance"]["status"], "success")
        self.assertNotIn("anchors", context["structural_guidance"]["maps"][0])
        self.assertEqual(context["sources"][0]["source_kind"], "derived_structure")
        self.assertIn("derived_structure", context["instructions"])

    def test_context_navigates_semantic_map_then_fetches_primary_chunks(self) -> None:
        service = api.RagApiService(self.settings())
        initial = {
            "query": "Onde a malha é inicializada?",
            "mode": "hybrid",
            "count": 1,
            "scope_resolution": {
                "mode": "explicit",
                "automatic": False,
                "scopes": [{"project": "Solver", "branch": "trunk"}],
            },
            "results": [
                {
                    "chunk_id": "weak",
                    "project": "Solver",
                    "path": "src/model.cpp",
                    "title": "Model::initialize",
                    "text": "void Model::initialize() {}",
                    "selected_occurrence": {
                        "branch": "trunk",
                        "commit_sha": "a" * 40,
                    },
                }
            ],
        }
        node = {
            "item_id": "symbol-1",
            "project": "Solver",
            "path": "src/mesh.cpp",
            "qualified_name": "Mesh::initialize",
            "evidence_chunk_id": "mesh-evidence",
            "selected_occurrence": {
                "branch": "trunk",
                "commit_sha": "a" * 40,
            },
        }
        primary = {
            "chunk_id": "mesh-evidence",
            "project": "Solver",
            "path": "src/mesh.cpp",
            "title": "Mesh::initialize",
            "text": "void Mesh::initialize() { build(); }",
            "selected_occurrence": {
                "branch": "trunk",
                "commit_sha": "a" * 40,
            },
        }
        with mock.patch.object(service, "search", return_value=initial):
            with mock.patch.object(
                api, "search_semantic_map", return_value=[node]
            ) as map_search:
                with mock.patch.object(
                    api, "fetch_chunks_by_id", return_value=[primary]
                ) as fetch:
                    context = service.context(
                        query="Onde a malha é inicializada?",
                        project="Solver",
                        branch="trunk",
                        allowed_access={"lab"},
                        query_plan={
                            "algorithm": "test",
                            "generated": True,
                            "queries": ["Onde a malha é inicializada?"],
                            "identifiers": ["Mesh::initialize"],
                        },
                    )

        self.assertGreaterEqual(map_search.call_count, 1)
        self.assertEqual(fetch.call_args.kwargs["project"], "Solver")
        self.assertEqual(fetch.call_args.kwargs["branch"], "trunk")
        self.assertEqual(context["sources"][0]["path"], "src/mesh.cpp")
        self.assertEqual(
            context["sources"][0]["source_kind"],
            "structural_navigation_evidence",
        )
        self.assertEqual(
            context["structural_guidance"]["navigation_status"], "success"
        )

    def test_context_iteratively_chooses_tools_after_observing_results(self) -> None:
        investigator = _InvestigatingGenerator()
        service = api.RagApiService(self.settings(), generator=investigator)
        initial = {
            "query": "Onde o componente é inicializado?",
            "mode": "hybrid",
            "count": 1,
            "scope_resolution": {
                "mode": "explicit",
                "automatic": False,
                "scopes": [{"project": "Solver", "branch": "trunk"}],
            },
            "results": [
                {
                    "chunk_id": "weak",
                    "project": "Solver",
                    "path": "src/unrelated.cpp",
                    "title": "Unrelated::initialize",
                    "text": "void Unrelated::initialize() {}",
                    "selected_occurrence": {
                        "branch": "trunk",
                        "commit_sha": "a" * 40,
                    },
                }
            ],
        }
        expanded = {
            **initial,
            "query": "factory create initialize",
            "results": [
                {
                    "chunk_id": "correct",
                    "project": "Solver",
                    "path": "src/domain.cpp",
                    "title": "Domain::setup",
                    "text": "object = Factory::create(); object->initialize();",
                    "selected_occurrence": {
                        "branch": "trunk",
                        "commit_sha": "a" * 40,
                    },
                }
            ],
        }

        def search(**values: object) -> dict[str, object]:
            if values["query"] == "factory create initialize":
                return expanded
            if values["query"] == "unobserved guessed helper":
                return {**initial, "query": values["query"], "results": []}
            return initial

        progress: list[dict[str, object]] = []
        with mock.patch.object(service, "search", side_effect=search) as search_mock:
            with mock.patch.object(api, "search_semantic_map", return_value=[]):
                context = service.context(
                    query="Onde o componente é inicializado?",
                    project="Solver",
                    branch="trunk",
                    allowed_access={"lab"},
                    query_plan={
                        "algorithm": "test",
                        "generated": True,
                        "queries": ["Onde o componente é inicializado?"],
                        "identifiers": [],
                        "aspects": ["entry point"],
                    },
                    progress_callback=progress.append,
                )

        self.assertEqual(investigator.calls, 3)
        self.assertEqual(
            investigator.history[0]["previous_coverage"],
            [
                {
                    "aspect": "entry point",
                    "status": "gap",
                    "chunk_ids": [],
                }
            ],
        )
        self.assertEqual(search_mock.call_count, 3)
        self.assertEqual(context["agent_investigation"]["status"], "sufficient")
        self.assertEqual(context["agent_investigation"]["iterations"], 3)
        self.assertEqual(context["sources"][0]["path"], "src/domain.cpp")
        self.assertIn("agent", {step["stage"] for step in progress})

    def test_context_replans_an_inconclusive_agent_decision(self) -> None:
        investigator = _ReplanningGenerator()
        service = api.RagApiService(self.settings(), generator=investigator)
        initial = {
            "query": "How does the component work?",
            "mode": "hybrid",
            "count": 1,
            "scope_resolution": {
                "mode": "explicit",
                "automatic": False,
                "scopes": [{"project": "Solver", "branch": "trunk"}],
            },
            "results": [
                {
                    "chunk_id": "weak",
                    "project": "Solver",
                    "path": "src/component.cpp",
                    "title": "Component::run",
                    "text": "void Component::run() {}",
                    "selected_occurrence": {
                        "branch": "trunk",
                        "commit_sha": "a" * 40,
                    },
                }
            ],
        }
        progress: list[dict[str, object]] = []
        with mock.patch.object(service, "search", return_value=initial):
            with mock.patch.object(api, "search_semantic_map", return_value=[]):
                context = service.context(
                    query="How does the component work?",
                    project="Solver",
                    branch="trunk",
                    allowed_access={"lab"},
                    query_plan={
                        "algorithm": "test",
                        "generated": True,
                        "queries": ["How does the component work?"],
                        "identifiers": [],
                    },
                    progress_callback=progress.append,
                )

        self.assertEqual(len(investigator.calls), 6)
        self.assertEqual(
            investigator.calls[0].get("decision_feedback"), ""
        )
        self.assertIn(
            "neither stopped nor selected a tool",
            str(investigator.calls[1].get("decision_feedback")),
        )
        self.assertEqual(
            context["agent_investigation"]["status"], "budget_exhausted"
        )
        self.assertIn(
            "Cobertura final reconciliada",
            [step["title"] for step in progress],
        )
        self.assertIn(
            "Decisão inconclusiva será reavaliada",
            [step["title"] for step in progress],
        )

    def test_context_recovers_invalid_decision_with_observed_targets(self) -> None:
        investigator = _InvalidThenStoppingGenerator()
        service = api.RagApiService(self.settings(), generator=investigator)
        initial = {
            "query": "Where is the adaptive grid built?",
            "mode": "hybrid",
            "count": 1,
            "scope_resolution": {
                "mode": "explicit",
                "automatic": False,
                "scopes": [{"project": "Solver", "branch": "trunk"}],
            },
            "results": [
                {
                    "chunk_id": "observed",
                    "project": "Solver",
                    "path": "src/grid/manager.cpp",
                    "title": "GridManager::buildAdaptive",
                    "text": "void GridManager::buildAdaptive() { refine(); }",
                    "selected_occurrence": {
                        "branch": "trunk",
                        "commit_sha": "a" * 40,
                    },
                }
            ],
        }
        progress: list[dict[str, object]] = []
        with mock.patch.object(service, "search", return_value=initial):
            with mock.patch.object(api, "search_semantic_map", return_value=[]):
                with mock.patch.object(
                    api,
                    "fetch_chunk_neighborhood",
                    return_value=initial["results"],
                ):
                    context = service.context(
                        query="Where is the adaptive grid built?",
                        project="Solver",
                        branch="trunk",
                        allowed_access={"lab"},
                        query_plan={
                            "algorithm": "test",
                            "generated": True,
                            "queries": ["adaptive grid construction"],
                            "identifiers": [],
                        },
                        progress_callback=progress.append,
                    )

        self.assertEqual(investigator.calls, 2)
        self.assertEqual(context["agent_investigation"]["status"], "sufficient")
        self.assertGreaterEqual(len(context["agent_investigation"]["actions"]), 2)
        self.assertIn(
            "Leitura de contingência selecionada",
            [step["title"] for step in progress],
        )

    def test_context_follows_resolved_callers_and_callees(self) -> None:
        generator = _CallGraphGenerator()
        service = api.RagApiService(self.settings(), generator=generator)
        occurrence = {
            "branch": "trunk",
            "commit_sha": "a" * 40,
        }
        initial_result = {
            "chunk_id": "observed",
            "project": "Solver",
            "path": "src/component.cpp",
            "title": "Component::run",
            "text": "void Component::run() { helper(); }",
            "selected_occurrence": occurrence,
        }
        graph_results = {
            "caller": {
                **initial_result,
                "chunk_id": "caller",
                "path": "src/driver.cpp",
                "title": "Driver::advance",
            },
            "callee": {
                **initial_result,
                "chunk_id": "callee",
                "path": "src/helper.cpp",
                "title": "helper",
            },
        }

        def call_ids(*_args: object, **values: object) -> list[str]:
            return ["caller" if values["direction"] == "callers" else "callee"]

        def fetch(*_args: object, **values: object) -> list[dict[str, object]]:
            return [graph_results[value] for value in values["chunk_ids"]]

        initial = {
            "query": "Explain the component call flow",
            "mode": "hybrid",
            "count": 1,
            "scope_resolution": {
                "mode": "explicit",
                "automatic": False,
                "scopes": [{"project": "Solver", "branch": "trunk"}],
            },
            "results": [initial_result],
        }
        with mock.patch.object(service, "search", return_value=initial):
            with mock.patch.object(api, "search_semantic_map", return_value=[]):
                with mock.patch.object(
                    api, "call_graph_chunk_ids", side_effect=call_ids
                ) as graph:
                    with mock.patch.object(
                        api, "fetch_chunks_by_id", side_effect=fetch
                    ):
                        with mock.patch.object(
                            api, "fetch_chunk_neighborhood", return_value=[]
                        ):
                            context = service.context(
                                query="Explain the component call flow",
                                project="Solver",
                                branch="trunk",
                                allowed_access={"lab"},
                                query_plan={
                                    "algorithm": "test",
                                    "generated": True,
                                    "queries": ["component call flow"],
                                    "identifiers": [],
                                },
                            )

        self.assertEqual(generator.calls, 2, generator.history)
        self.assertEqual(graph.call_count, 4)
        self.assertEqual(
            {call.kwargs["direction"] for call in graph.call_args_list},
            {"callers", "callees"},
        )
        self.assertTrue(
            {"src/driver.cpp", "src/helper.cpp"}.issubset(
                {source["path"] for source in context["sources"]}
            )
        )
        self.assertEqual(
            context["agent_investigation"]["graph_frontier_chunk_ids"],
            ["caller", "callee"],
        )
        self.assertEqual(
            context["agent_investigation"]["lineage_origin_chunk_ids"],
            ["observed", "caller"],
        )
        self.assertEqual(
            context["agent_investigation"]["lineage_flow_chunk_ids"],
            ["observed", "callee", "caller"],
        )
        self.assertEqual(
            context["agent_investigation"]["lineage_graph_chunk_ids"],
            ["callee"],
        )
        self.assertIn(
            {
                "origin_chunk_id": "observed",
                "target_chunk_id": "callee",
                "kind": "calls_symbol",
            },
            context["agent_investigation"]["lineage_edges"],
        )
        self.assertEqual(
            [
                item["path"]
                for item in context["agent_investigation"]["graph_frontier"]
            ],
            ["src/driver.cpp", "src/helper.cpp"],
        )
        self.assertEqual(context["investigation_graph"]["status"], "available")
        self.assertGreaterEqual(context["investigation_graph"]["edge_count"], 2)
        self.assertTrue(
            {
                ("chunk:caller", "chunk:observed", "calls"),
                ("chunk:observed", "chunk:callee", "calls"),
            }.issubset(
                {
                    (edge["source"], edge["target"], edge["kind"])
                    for edge in context["investigation_graph"]["edges"]
                }
            )
        )

    def test_ask_audits_long_answers_in_bounded_batches(self) -> None:
        answer = "\n\n".join(
            f"Claim {position} is supported [S1]." for position in range(1, 8)
        )
        audits = []
        for identifiers in ((1, 2, 3, 4, 5), (6, 7)):
            items = ",".join(
                '{"claim_id":"C%s","verdict":"supported",'
                '"source_ids":["S1"],"finding":"Present."}' % identifier
                for identifier in identifiers
            )
            audits.append('{"claims":[' + items + "]}")
        generator = _VerifyingGenerator(answers=[answer], audits=audits)
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_repair_attempts=0,
            ),
        )
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the flow",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 20,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/model.cpp",
                        "text": "Implementation evidence.",
                    }
                ],
            },
        ):
            result = service.ask(query="Explain the flow")

        self.assertTrue(result["verification"]["passed"])
        self.assertEqual(result["verification"]["batches"], 2)
        self.assertEqual(result["verification"]["counts"]["supported"], 7)
        self.assertEqual(len(generator.verify_calls), 2)
        self.assertEqual(
            [
                call["answer"].count("Claim ")
                for call in generator.verify_calls
            ],
            [5, 2],
        )
        self.assertNotIn("Claim 6", generator.verify_calls[0]["answer"])
        self.assertNotIn("Claim 1", generator.verify_calls[1]["answer"])

    def test_ask_applies_detailed_response_depth_to_generation(self) -> None:
        generator = _Generator("The flow is established [S1].")
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                verify_evidence=False,
            ),
        )
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the complete flow",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "exploration": {"intent": "mechanism"},
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 30,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "path": "src/solver.cpp",
                        "text": "void advance() {}",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                    }
                ],
                "investigation": {"steps": []},
            },
        ):
            result = service.ask(
                query="Explain the complete flow",
                response_depth="detailed",
            )

        self.assertIn(
            "detailed technical explanation",
            generator.calls[0]["instructions"],
        )
        self.assertEqual(result["context"]["response_depth"], "detailed")

    def test_ask_synthesizes_evidenced_facets_in_separate_bounded_calls(
        self,
    ) -> None:
        generator = _SequencedGenerator(
            [
                "## Entrada\n\nThe entry point is shown here [S1].",
                "The local setup continues [S1].",
                "The local setup is then completed [S1].",
                "## Avanço\n\nThe advancement stage is shown here [S2].",
            ],
            finish_reasons=["length", "length", "stop", "stop"],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_output_tokens=4096,
                verify_evidence=False,
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "chunk_id": "entry",
                "project": "Solver",
                "path": "src/entry.cpp",
                "text": "void enter() {}",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
            },
            {
                "source_id": "S2",
                "chunk_id": "advance",
                "project": "Solver",
                "path": "src/advance.cpp",
                "text": "void advance() {}",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
            },
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the complete mechanism",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "exploration": {"intent": "mechanism"},
                "agent_investigation": {
                    "coverage": [
                        {
                            "aspect_id": "A1",
                            "aspect": "entry point",
                            "status": "covered",
                            "chunk_ids": ["entry"],
                        },
                        {
                            "aspect_id": "A2",
                            "aspect": "advancement",
                            "status": "covered",
                            "chunk_ids": ["advance"],
                        },
                    ]
                },
                "retrieved_count": 2,
                "source_count": 2,
                "context_characters": 40,
                "truncated": False,
                "sources": sources,
                "investigation": {"steps": []},
            },
        ):
            result = service.ask(
                query="Explain the complete mechanism",
                response_depth="detailed",
            )

        self.assertEqual(len(generator.calls), 4)
        self.assertEqual(generator.calls[0]["sources"], [sources[0]])
        self.assertEqual(generator.calls[1]["sources"], [sources[0]])
        self.assertEqual(generator.calls[2]["sources"], [sources[0]])
        self.assertEqual(generator.calls[3]["sources"], [sources[1]])
        self.assertEqual(generator.calls[0]["max_output_tokens"], 3072)
        self.assertEqual(generator.calls[1]["max_output_tokens"], 1536)
        self.assertEqual(generator.calls[2]["max_output_tokens"], 1536)
        self.assertIn("SECTIONAL NARRATIVE CONTRACT", generator.calls[0]["instructions"])
        self.assertIn("SECTION CONTINUATION CONTRACT", generator.calls[1]["instructions"])
        self.assertNotIn("##", result["answer"])
        self.assertIn("The entry point", result["answer"])
        self.assertIn("local setup", result["answer"])
        self.assertIn("The advancement stage", result["answer"])
        self.assertTrue(result["context"]["sectional_synthesis"])
        self.assertEqual(result["context"]["section_generation_count"], 2)
        self.assertEqual(result["context"]["section_continuation_count"], 2)
        self.assertEqual(result["context"]["generation_attempts"], 4)
        self.assertEqual(result["usage"]["total_tokens"], 60)
        self.assertEqual(result["finish_reason"], "stop")

    def test_ask_preserves_grounded_sections_without_global_rewrite(self) -> None:
        generator = _ComposingGenerator(
            [
                "## Entry\n\nThe entry is visible [S1].",
                "## Advance\n\nThe advance is visible [S2].",
            ],
            (
                "## Flow\n\nThe entry is visible [S1].\n\n"
                "The advance is visible [S2]."
            ),
            context_failures=1,
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_output_tokens=4096,
                verify_evidence=False,
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "chunk_id": "entry",
                "project": "Solver",
                "path": "src/entry.cpp",
                "text": "void enter() {}",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
            },
            {
                "source_id": "S2",
                "chunk_id": "advance",
                "project": "Solver",
                "path": "src/advance.cpp",
                "text": "void advance() {}",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
            },
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the complete flow",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "exploration": {"intent": "mechanism"},
                "agent_investigation": {
                    "coverage": [
                        {
                            "aspect_id": "A1",
                            "aspect": "entry point",
                            "status": "covered",
                            "chunk_ids": ["entry"],
                        },
                        {
                            "aspect_id": "A2",
                            "aspect": "advancement",
                            "status": "covered",
                            "chunk_ids": ["advance"],
                        },
                    ]
                },
                "retrieved_count": 2,
                "source_count": 2,
                "context_characters": 40,
                "truncated": False,
                "sources": sources,
                "investigation": {"steps": []},
            },
        ):
            result = service.ask(
                query="Explain the complete flow",
                response_depth="detailed",
            )

        self.assertEqual(len(generator.composition_calls), 0)
        self.assertNotIn("##", result["answer"])
        self.assertIn("The entry is visible", result["answer"])
        self.assertIn("The advance is visible", result["answer"])
        self.assertTrue(result["context"]["sectional_synthesis"])
        self.assertFalse(result["context"]["section_composition"])
        self.assertFalse(result["context"]["section_composition_attempted"])
        self.assertEqual(result["context"]["section_composition_attempts"], 0)
        self.assertFalse(result["context"]["section_composition_reduced"])
        self.assertIsNone(
            result["context"]["section_composition_max_output_tokens"]
        )
        self.assertEqual(result["context"]["generation_attempts"], 2)
        self.assertEqual(result["usage"]["total_tokens"], 30)

    def test_ask_uses_local_query_planner_for_location_questions(self) -> None:
        generator = _PlanningGenerator()
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                verify_evidence=False,
            ),
        )
        captured: dict[str, object] = {}

        def context(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "query": kwargs["query"],
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "exploration": {"intent": "location"},
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 20,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "path": "src/mesh.cpp",
                        "text": "void initialize() {}",
                        "selected_occurrence": {
                            "branch": "trunk",
                            "commit_sha": "a" * 40,
                        },
                    }
                ],
                "investigation": {"steps": []},
            }

        with mock.patch.object(service, "context", side_effect=context):
            result = service.ask(query="Onde a malha é inicializada?")

        self.assertFalse(result["abstained"])
        self.assertEqual(len(generator.plan_calls), 1)
        query_plan = captured["query_plan"]
        self.assertIsInstance(query_plan, dict)
        self.assertIn("MeshFactory", query_plan["identifiers"])
        self.assertEqual(
            query_plan["aspects"],
            ["initialization"],
        )
        self.assertEqual(
            query_plan["aspect_anchors"],
            [{"aspect": "initialization", "question_span": "inicializada"}],
        )

    def test_structural_anchor_marks_an_existing_search_result(self) -> None:
        result = {
            "chunk_id": "shared",
            "project": "Solver",
            "path": "README.md",
            "selected_occurrence": {"branch": "trunk"},
        }
        anchor = {**result, "source_kind": "primary_structure_anchor"}

        merged = api._merge_exploration_results(
            [{"results": [result]}, {"results": [anchor]}],
            limit=2,
            overview=True,
        )

        self.assertEqual(merged[0]["source_kind"], "primary_structure_anchor")

    def test_reserved_graph_result_survives_merge_limit(self) -> None:
        retrievals = [
            {
                "results": [
                    {
                        "chunk_id": str(position),
                        "project": "Solver",
                        "path": f"src/unit_{position}.cpp",
                        "selected_occurrence": {"branch": "trunk"},
                    }
                    for position in range(20)
                ]
            }
        ]

        merged = api._merge_exploration_results(
            retrievals,
            limit=4,
            overview=False,
            reserved_chunk_ids=["17", "3"],
        )

        self.assertEqual(
            [result["chunk_id"] for result in merged],
            ["17", "3", "0", "1"],
        )

    def test_ask_validates_citations_and_reports_distinct_scopes(self) -> None:
        generator = _Generator("Compare [S1] with [S2]; ignore [S99].")
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "project": "Solver A",
                "selected_occurrence": {
                    "branch": "trunk",
                    "commit_sha": "a" * 40,
                },
                "path": "src/a.cpp",
                "text": "first",
            },
            {
                "source_id": "S2",
                "project": "Solver B",
                "selected_occurrence": {
                    "branch": "dev/feature",
                    "commit_sha": "b" * 40,
                },
                "path": "src/b.cpp",
                "text": "second",
            },
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "compare",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 2,
                "source_count": 2,
                "context_characters": 11,
                "truncated": False,
                "sources": sources,
            },
        ):
            result = service.ask(query="compare")

        self.assertEqual(result["grounding_status"], "invalid_citations")
        self.assertEqual(result["citations_used"], ["S1", "S2"])
        self.assertEqual(result["invalid_citations"], ["S99"])
        self.assertEqual(result["citation_coverage"]["coverage"], 1.0)
        self.assertTrue(result["scope_warning"])
        self.assertEqual(len(result["scopes"]), 2)
        self.assertNotIn("text", result["sources"][0])
        self.assertEqual(len(generator.calls), 1)

    def test_ask_returns_only_sources_used_by_the_final_answer(self) -> None:
        generator = _Generator("The selected implementation is shown [S1].")
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "project": "Solver",
                "selected_occurrence": {
                    "branch": "trunk",
                    "commit_sha": "a" * 40,
                },
                "path": "src/selected.cpp",
                "text": "selected implementation",
            },
            {
                "source_id": "S2",
                "project": "Solver",
                "selected_occurrence": {
                    "branch": "trunk",
                    "commit_sha": "a" * 40,
                },
                "path": "src/incidental.cpp",
                "text": "incidental implementation",
            },
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "selected implementation",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 2,
                "source_count": 2,
                "context_characters": 48,
                "truncated": False,
                "sources": sources,
            },
        ):
            result = service.ask(query="selected implementation")

        self.assertEqual(
            [source["source_id"] for source in result["sources"]],
            ["S1"],
        )

    def test_ask_abstains_without_calling_generator_when_no_sources(self) -> None:
        generator = _Generator()
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "unknown",
                "mode": "lexical",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 0,
                "source_count": 0,
                "context_characters": 0,
                "truncated": False,
                "sources": [],
            },
        ):
            result = service.ask(query="unknown", mode="lexical")

        self.assertTrue(result["abstained"])
        self.assertEqual(result["grounding_status"], "no_sources")
        self.assertEqual(result["citations_used"], [])
        self.assertEqual(result["invalid_citations"], [])
        self.assertIsNone(result["citation_coverage"]["coverage"])
        self.assertEqual(result["scopes"], [])
        self.assertFalse(result["scope_warning"])
        self.assertEqual(result["model"], "local-test-model")
        self.assertEqual(generator.calls, [])

    def test_ask_reports_partial_citation_coverage(self) -> None:
        generator = _Generator(
            "The solver initializes the state [S1].\n\n"
            "It also performs an unsupported operation."
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "explain",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 10,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "trunk",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/a.cpp",
                        "text": "evidence",
                    }
                ],
            },
        ):
            result = service.ask(query="explain")

        self.assertEqual(result["grounding_status"], "partial_citations")
        self.assertEqual(result["citation_coverage"]["units"], 2)
        self.assertEqual(result["citation_coverage"]["cited_units"], 1)
        self.assertEqual(result["citation_coverage"]["coverage"], 0.5)

    def test_ask_discovers_then_reaudits_support_for_uncited_units(self) -> None:
        generator = _SupportDiscoveringGenerator(
            answers=[
                "The operation advances state.\n\n"
                "This describes the complete architecture."
            ],
            discoveries=[
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported",'
                '"source_ids":["S1"],"finding":"Directly present."},'
                '{"claim_id":"C2","verdict":"unsupported",'
                '"source_ids":[],"finding":"Not established."}]}'
            ],
            audits=[
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported",'
                '"source_ids":["S1"],"finding":"Directly present."},'
                '{"claim_id":"C2","verdict":"unsupported",'
                '"source_ids":[],"finding":"Not cited or established."}]}',
                '{"claims":[{"claim_id":"C1","verdict":"supported",'
                '"source_ids":["S1"],"finding":"Directly present."}]}',
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_repair_attempts=0,
            ),
        )
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the operation",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 30,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/model.cpp",
                        "text": "The operation advances state.",
                    }
                ],
            },
        ):
            result = service.ask(query="Explain the operation")

        self.assertFalse(result["abstained"])
        self.assertEqual(result["answer"], "The operation advances state. [S1]")
        self.assertTrue(result["context"]["citation_discovery"])
        self.assertEqual(len(generator.discovery_calls), 1)
        self.assertEqual(len(generator.verify_calls), 1)

    def test_ask_repairs_a_cited_claim_that_the_source_does_not_support(self) -> None:
        generator = _VerifyingGenerator(
            answers=[
                "This function initializes the complete mesh [S1].",
                "The retrieved function assigns a pointer; the evidence does not "
                "establish mesh initialization [S1].",
            ],
            audits=[
                '{"claims":[{"claim_id":"C1","verdict":"unsupported",'
                '"source_ids":["S1"],"finding":"The source only assigns a local pointer."}]}',
                '{"claims":[{"claim_id":"C1","verdict":"supported",'
                '"source_ids":["S1"],"finding":"The answer now states the source limitation."}]}',
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "project": "Solver",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
                "path": "src/model.cpp",
                "text": "void initialize(Mesh* mesh) { mesh = _mesh; }",
            }
        ]
        progress: list[dict[str, object]] = []
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Where is the mesh initialized?",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 44,
                "truncated": False,
                "sources": sources,
                "investigation": {"steps": []},
            },
        ):
            result = service.ask(
                query="Where is the mesh initialized?",
                progress_callback=progress.append,
            )

        self.assertFalse(result["abstained"])
        self.assertTrue(result["verification"]["passed"])
        self.assertTrue(result["context"]["evidence_repair"])
        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(len(generator.verify_calls), 2)
        repair_instructions = str(generator.calls[1]["instructions"])
        self.assertIn(
            "This function initializes the complete mesh",
            repair_instructions,
        )
        self.assertIn(
            "The source only assigns a local pointer",
            repair_instructions,
        )
        self.assertIn("preserving useful supported statements", repair_instructions)
        self.assertIn("does not establish", result["answer"])
        self.assertIn("revision", [step["stage"] for step in progress])
        self.assertIn(
            "Revisão conferida contra as fontes",
            [step["title"] for step in progress],
        )

    def test_ask_retries_a_malformed_verification_without_regenerating(self) -> None:
        generator = _VerifyingGenerator(
            answers=["The implementation is located here [S1]."],
            audits=[
                "not valid structured output",
                '{"claims":[{"claim_id":"C1","verdict":"supported",'
                '"source_ids":["S1"],"finding":"The definition is present."}]}',
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        progress: list[dict[str, object]] = []
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Where is the implementation?",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 20,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/model.cpp",
                        "text": "void implementation() {}",
                    }
                ],
            },
        ):
            result = service.ask(
                query="Where is the implementation?",
                progress_callback=progress.append,
            )

        self.assertFalse(result["abstained"])
        self.assertTrue(result["verification"]["passed"])
        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(len(generator.verify_calls), 2)
        self.assertIn(
            "Conferência estruturada será repetida",
            [step["title"] for step in progress],
        )

    def test_ask_abstains_when_repair_remains_unsupported(self) -> None:
        audit = (
            '{"claims":[{"claim_id":"C1","verdict":"unsupported",'
            '"source_ids":["S1"],"finding":"The claim is not established."}]}'
        )
        generator = _VerifyingGenerator(
            answers=["Unsupported conclusion [S1].", "Still unsupported [S1]."],
            audits=[audit, audit],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "question",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 8,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/model.cpp",
                        "text": "evidence",
                    }
                ],
            },
        ):
            result = service.ask(query="question")

        self.assertTrue(result["abstained"])
        self.assertIsNone(result["answer"])
        self.assertEqual(result["reason"], "evidence_not_supported")
        self.assertEqual(result["grounding_status"], "evidence_not_supported")

    def test_ask_salvages_only_audited_claims_after_imperfect_repair(self) -> None:
        first_audit = (
            '{"claims":['
            '{"claim_id":"C1","verdict":"supported",'
            '"source_ids":["S1"],"finding":"Directly present."},'
            '{"claim_id":"C2","verdict":"unsupported",'
            '"source_ids":["S1"],"finding":"Too broad."}]}'
        )
        repaired_audit = (
            '{"claims":['
            '{"claim_id":"C1","verdict":"supported",'
            '"source_ids":["S1"],"finding":"Directly present."},'
            '{"claim_id":"C2","verdict":"unsupported",'
            '"source_ids":["S1"],"finding":"Still too broad."}]}'
        )
        final_audit = (
            '{"claims":[{"claim_id":"C1","verdict":"supported",'
            '"source_ids":["S1"],"finding":"Directly present."}]}'
        )
        generator = _VerifyingGenerator(
            answers=[
                "The operation advances state [S1].\n\n"
                "This is the complete architecture [S1].",
                "The operation advances state [S1].\n\n"
                "This covers the entire system [S1].",
            ],
            audits=[first_audit, repaired_audit, final_audit],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        progress: list[dict[str, object]] = []
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the operation",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 40,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/operation.cpp",
                        "text": "void advance_state() {}",
                    }
                ],
                "investigation": {"steps": []},
            },
        ):
            result = service.ask(
                query="Explain the operation",
                progress_callback=progress.append,
            )

        self.assertFalse(result["abstained"])
        self.assertEqual(result["answer"], "The operation advances state [S1].")
        self.assertTrue(result["verification"]["passed"])
        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(len(generator.verify_calls), 2)
        self.assertIn(
            "Afirmações rejeitadas removidas",
            [step["title"] for step in progress],
        )

    def test_ask_salvages_audited_claims_when_model_repair_is_disabled(self) -> None:
        generator = _VerifyingGenerator(
            answers=[
                "The observed operation advances state [S1].\n\n"
                "This is the complete architecture [S1]."
            ],
            audits=[
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported",'
                '"source_ids":["S1"],"finding":"Directly present."},'
                '{"claim_id":"C2","verdict":"unsupported",'
                '"source_ids":["S1"],"finding":"Too broad."}]}',
                '{"claims":[{"claim_id":"C1","verdict":"supported",'
                '"source_ids":["S1"],"finding":"Directly present."}]}',
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_repair_attempts=0,
            ),
        )
        progress: list[dict[str, object]] = []
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the observed operation",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 30,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/model.cpp",
                        "text": "The operation advances state.",
                    }
                ],
            },
        ):
            result = service.ask(
                query="Explain the observed operation",
                progress_callback=progress.append,
            )

        self.assertFalse(result["abstained"])
        self.assertTrue(result["verification"]["passed"])
        self.assertEqual(
            result["answer"], "The observed operation advances state [S1]."
        )
        self.assertFalse(result["context"]["evidence_repair"])
        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(len(generator.verify_calls), 1)
        self.assertIn(
            "Afirmações rejeitadas removidas",
            [step["title"] for step in progress],
        )

    def test_detailed_salvage_is_not_presented_as_a_complete_answer(self) -> None:
        generator = _VerifyingGenerator(
            answers=[
                "The observed operation advances state [S1].\n\n"
                "This is the complete architecture [S1]."
            ],
            audits=[
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported",'
                '"source_ids":["S1"],"finding":"Directly present."},'
                '{"claim_id":"C2","verdict":"unsupported",'
                '"source_ids":["S1"],"finding":"Too broad."}]}',
                '{"claims":[{"claim_id":"C1","verdict":"supported",'
                '"source_ids":["S1"],"finding":"Directly present."}]}',
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_repair_attempts=0,
            ),
        )
        progress: list[dict[str, object]] = []
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the complete operation",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 30,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/model.cpp",
                        "text": "The operation advances state.",
                    }
                ],
            },
        ):
            result = service.ask(
                query="Explain the complete operation",
                response_depth="detailed",
                progress_callback=progress.append,
            )

        self.assertFalse(result["abstained"])
        self.assertEqual(result["answer_completeness"], "supported_subset")
        self.assertEqual(
            result["answer"],
            "The observed operation advances state [S1].",
        )
        self.assertIn(
            "Investigação concluída com limitações",
            [step["title"] for step in progress],
        )

    def test_audited_answer_coverage_can_confirm_a_complete_salvaged_answer(
        self,
    ) -> None:
        generator = _CoverageVerifyingGenerator(
            answers=[
                "The runtime advances state [S1].\n\n"
                "This proves an unrelated feature [S1]."
            ],
            audits=[
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported","source_ids":["S1"]},'
                '{"claim_id":"C2","verdict":"unsupported","source_ids":["S1"]}]}',
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported","source_ids":["S1"]}]}',
            ],
            coverage_audits=[
                '{"coverage":[{"aspect":"runtime flow","status":"covered",'
                '"claim_ids":["C1"]}]}'
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_repair_attempts=0,
            ),
        )
        progress: list[dict[str, object]] = []
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the runtime flow",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 40,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/runtime.cpp",
                        "text": "The runtime advances state.",
                    }
                ],
                "exploration": {
                    "intent": "mechanism",
                    "query_plan": {"aspects": ["runtime flow"]},
                },
                "agent_investigation": {
                    "coverage": [
                        {
                            "aspect": "runtime flow",
                            "status": "partial",
                            "chunk_ids": ["runtime"],
                        }
                    ]
                },
                "investigation": {"steps": []},
            },
        ):
            result = service.ask(
                query="Explain the runtime flow",
                response_depth="detailed",
                progress_callback=progress.append,
            )

        self.assertFalse(result["abstained"])
        self.assertEqual(result["answer_completeness"], "complete")
        self.assertTrue(result["answer_coverage"]["complete"])
        self.assertEqual(result["answer"], "The runtime advances state [S1].")
        self.assertEqual(len(generator.coverage_calls), 1)
        self.assertEqual(
            generator.coverage_calls[0]["aspects"],
            [
                {
                    "aspect": "runtime flow",
                    "question_span": "runtime flow",
                    "aspect_id": "A1",
                }
            ],
        )
        self.assertIn(
            "Cobertura da pergunta conferida",
            [step["title"] for step in progress],
        )

    def test_answer_coverage_audits_each_requested_aspect_independently(
        self,
    ) -> None:
        generator = _CoverageVerifyingGenerator(
            answers=["The runtime configures [S1].\n\nIt advances [S1]."],
            audits=[
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported","source_ids":["S1"]},'
                '{"claim_id":"C2","verdict":"supported","source_ids":["S1"]}]}'
            ],
            coverage_audits=[
                '{"coverage":[{"aspect_id":"A1","status":"covered",'
                '"claim_ids":["C1"]}]}',
                '{"coverage":[{"aspect_id":"A2","status":"covered",'
                '"claim_ids":["C2"]}]}',
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_repair_attempts=0,
            ),
        )
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain configuration and advancement",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 40,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                        "path": "src/runtime.cpp",
                        "text": "The runtime configures and advances.",
                    }
                ],
                "exploration": {
                    "intent": "mechanism",
                    "query_plan": {
                        "aspects": ["configuration", "advancement"],
                        "aspect_anchors": [
                            {
                                "aspect": "configuration",
                                "question_span": "configuration",
                            },
                            {
                                "aspect": "advancement",
                                "question_span": "advancement",
                            },
                        ],
                    },
                },
                "investigation": {"steps": []},
            },
        ):
            result = service.ask(
                query="Explain configuration and advancement",
                response_depth="detailed",
            )

        self.assertEqual(result["answer_completeness"], "complete")
        self.assertTrue(result["answer_coverage"]["complete"])
        self.assertEqual(len(generator.coverage_calls), 2)
        self.assertEqual(
            [call["aspects"][0]["aspect_id"] for call in generator.coverage_calls],
            ["A1", "A2"],
        )

    def test_answer_coverage_scopes_supported_claims_to_notebook_section(
        self,
    ) -> None:
        generator = _CoverageVerifyingGenerator(
            answers=[
                "## Configuration\n\nThe runtime configures state [S1].",
                "## Advancement\n\nThe runtime advances state [S2].",
            ],
            audits=[
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported","source_ids":["S1"]},'
                '{"claim_id":"C2","verdict":"supported","source_ids":["S2"]}]}'
            ],
            coverage_audits=[
                '{"coverage":[{"aspect_id":"A1","status":"covered",'
                '"claim_ids":["C1"]}]}',
                '{"coverage":[{"aspect_id":"A2","status":"covered",'
                '"claim_ids":["C2"]}]}',
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_repair_attempts=0,
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "chunk_id": "configure",
                "project": "Solver",
                "path": "src/configure.cpp",
                "text": "The runtime configures state.",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
            },
            {
                "source_id": "S2",
                "chunk_id": "advance",
                "project": "Solver",
                "path": "src/advance.cpp",
                "text": "The runtime advances state.",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
            },
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain configuration and advancement",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 2,
                "source_count": 2,
                "context_characters": 80,
                "truncated": False,
                "sources": sources,
                "exploration": {
                    "intent": "mechanism",
                    "query_plan": {
                        "aspects": ["configuration", "advancement"],
                        "aspect_anchors": [
                            {
                                "aspect": "configuration",
                                "question_span": "configuration",
                            },
                            {
                                "aspect": "advancement",
                                "question_span": "advancement",
                            },
                        ],
                    },
                },
                "agent_investigation": {
                    "coverage": [
                        {
                            "aspect": "configuration",
                            "status": "covered",
                            "chunk_ids": ["configure"],
                        },
                        {
                            "aspect": "advancement",
                            "status": "covered",
                            "chunk_ids": ["advance"],
                        },
                    ]
                },
                "investigation": {"steps": []},
            },
        ):
            result = service.ask(
                query="Explain configuration and advancement",
                response_depth="detailed",
            )

        self.assertEqual(result["answer_completeness"], "complete")
        self.assertEqual(len(generator.coverage_calls), 2)
        self.assertEqual(
            [
                [claim["claim_id"] for claim in call["supported_claims"]]
                for call in generator.coverage_calls
            ],
            [["C1"], ["C2"]],
        )

    def test_verified_section_contract_can_resolve_conservative_partial_labels(
        self,
    ) -> None:
        generator = _CoverageVerifyingGenerator(
            answers=[
                "The coordinator enters the worker [S1].\n\n"
                "The coordinator then advances it [S1].",
                "The worker advances state [S2].\n\n"
                "```cpp\nworker.advance();\n```\n\n[S2]",
            ],
            audits=[
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported","source_ids":["S1"]},'
                '{"claim_id":"C2","verdict":"supported","source_ids":["S1"]},'
                '{"claim_id":"C3","verdict":"supported","source_ids":["S2"]}]}'
            ],
            coverage_audits=[
                '{"coverage":[{"aspect_id":"A1","status":"partial",'
                '"claim_ids":["C1","C2"]}]}',
                '{"coverage":[{"aspect_id":"A2","status":"partial",'
                '"claim_ids":["C3"]}]}',
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_repair_attempts=0,
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "chunk_id": "caller",
                "project": "Solver",
                "path": "src/coordinator.cpp",
                "text": (
                    "The coordinator enters the worker and then advances it."
                ),
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
            },
            {
                "source_id": "S2",
                "chunk_id": "worker",
                "project": "Solver",
                "path": "src/worker.cpp",
                "text": "The worker advances state.\nworker.advance();",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
            },
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the runtime flow and show a code excerpt",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 2,
                "source_count": 2,
                "context_characters": 120,
                "truncated": False,
                "sources": sources,
                "exploration": {
                    "intent": "mechanism",
                    "query_plan": {
                        "aspect_anchors": [
                            {
                                "aspect": "runtime flow",
                                "question_span": "runtime flow",
                            },
                            {
                                "aspect": "code excerpt",
                                "question_span": "code excerpt",
                            },
                        ]
                    },
                },
                "agent_investigation": {
                    "coverage": [
                        {
                            "aspect": "runtime flow",
                            "status": "partial",
                            "chunk_ids": ["caller"],
                        },
                        {
                            "aspect": "code excerpt",
                            "status": "partial",
                            "chunk_ids": ["worker"],
                        },
                    ]
                },
                "investigation": {"steps": []},
            },
        ):
            result = service.ask(
                query="Explain the runtime flow and show a code excerpt",
                response_depth="detailed",
            )

        self.assertEqual(result["answer_completeness"], "complete")
        self.assertTrue(result["answer_coverage"]["complete"])
        self.assertEqual(
            result["answer_coverage"]["resolution"],
            "verified_answer_contract",
        )

    def test_deterministic_salvage_reuses_verdicts_for_identical_claims(self) -> None:
        generator = _VerifyingGenerator(
            answers=[
                "First fact [S1].\n\nSecond fact [S1].\n\nBroad claim [S1]."
            ],
            audits=[
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported","source_ids":["S1"]},'
                '{"claim_id":"C2","verdict":"supported","source_ids":["S1"]},'
                '{"claim_id":"C3","verdict":"unsupported","source_ids":["S1"]}]}',
                '{"claims":['
                '{"claim_id":"C1","verdict":"unsupported","source_ids":["S1"]},'
                '{"claim_id":"C2","verdict":"supported","source_ids":["S1"]}]}',
                '{"claims":['
                '{"claim_id":"C1","verdict":"supported","source_ids":["S1"]}]}',
            ],
        )
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_repair_attempts=0,
            ),
        )
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the flow",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 20,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "path": "src/model.cpp",
                        "text": "First fact. Second fact.",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                    }
                ],
            },
        ):
            result = service.ask(query="Explain the flow")

        self.assertFalse(result["abstained"])
        self.assertEqual(result["answer"], "First fact [S1].\n\nSecond fact [S1].")
        self.assertEqual(result["answer_completeness"], "supported_subset")
        self.assertEqual(len(generator.verify_calls), 1)

    def test_unresolved_coverage_is_not_reported_as_complete(self) -> None:
        generator = _Generator("The observed step advances state [S1].")
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                verify_evidence=False,
            ),
        )
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "Explain the complete flow",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 20,
                "truncated": False,
                "sources": [
                    {
                        "source_id": "S1",
                        "project": "Solver",
                        "path": "src/model.cpp",
                        "text": "advance state",
                        "selected_occurrence": {
                            "branch": "main",
                            "commit_sha": "a" * 40,
                        },
                    }
                ],
                "agent_investigation": {
                    "coverage": [
                        {
                            "aspect": "integration",
                            "status": "gap",
                            "chunk_ids": [],
                        }
                    ]
                },
            },
        ):
            result = service.ask(query="Explain the complete flow")

        self.assertFalse(result["abstained"])
        self.assertEqual(result["answer_completeness"], "coverage_limited")

    def test_overview_reports_when_answer_cites_only_one_scope(self) -> None:
        generator = _Generator("Solver A is the complete system [S1].")
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "project": "Solver A",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
                "path": "README.md",
                "text": "A",
            },
            {
                "source_id": "S2",
                "project": "Solver B",
                "selected_occurrence": {
                    "branch": "trunk",
                    "commit_sha": "b" * 40,
                },
                "path": "README.md",
                "text": "B",
            },
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "What is Solver?",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "exploration": {
                    "intent": "overview",
                    "require_scope_coverage": True,
                },
                "retrieved_count": 2,
                "source_count": 2,
                "context_characters": 2,
                "truncated": False,
                "sources": sources,
            },
        ):
            result = service.ask(query="What is Solver?")

        self.assertEqual(
            result["grounding_status"], "incomplete_scope_coverage"
        )
        self.assertEqual(result["scope_citation_coverage"]["coverage"], 0.5)
        self.assertEqual(
            result["scope_citation_coverage"]["missing_scopes"],
            [{"project": "Solver B", "branch": "trunk"}],
        )
        self.assertTrue(result["context"]["quality_retry"])
        self.assertEqual(result["context"]["generation_attempts"], 2)

    def test_overview_retry_can_remove_scope_overclaim(self) -> None:
        generator = _Generator()
        answers = iter(
            [
                "The main projects are Solver A and Solver B [S1, S2].",
                "Available indexed scopes include Solver A and Solver B [S1, S2].",
            ]
        )

        def generate(**kwargs: object) -> dict[str, object]:
            generator.calls.append(kwargs)
            return {
                "answer": next(answers),
                "model": "local-test-model",
                "finish_reason": "stop",
                "usage": {"total_tokens": 20},
            }

        generator.generate = generate  # type: ignore[method-assign]
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "project": "Solver A",
                "selected_occurrence": {
                    "branch": "main",
                    "commit_sha": "a" * 40,
                },
                "path": "README.md",
                "text": "A",
            },
            {
                "source_id": "S2",
                "project": "Solver B",
                "selected_occurrence": {
                    "branch": "trunk",
                    "commit_sha": "b" * 40,
                },
                "path": "README.md",
                "text": "B",
            },
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "What is Solver?",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "exploration": {
                    "intent": "overview",
                    "require_scope_coverage": True,
                },
                "retrieved_count": 2,
                "source_count": 2,
                "context_characters": 2,
                "truncated": False,
                "sources": sources,
            },
        ):
            result = service.ask(query="What is Solver?")

        self.assertEqual(result["grounding_status"], "cited")
        self.assertEqual(result["scope_citation_coverage"]["coverage"], 1.0)
        self.assertEqual(result["context"]["generation_attempts"], 2)
        self.assertTrue(result["context"]["quality_retry"])
        self.assertEqual(result["overview_quality_issues"], [])
        self.assertIn("never call these", generator.calls[1]["instructions"])

    def test_ask_caps_and_reduces_context_when_provider_rejects_it(self) -> None:
        generator = _RetryGenerator()
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_context_characters=4000,
            ),
        )
        sources = [
            {
                "source_id": f"S{index}",
                "project": "Solver",
                "selected_occurrence": {
                    "branch": "trunk",
                    "commit_sha": "a" * 40,
                },
                "path": f"src/{index}.cpp",
                "text": character * 2000,
            }
            for index, character in ((1, "A"), (2, "B"))
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "explain",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 2,
                "source_count": 2,
                "context_characters": 4000,
                "max_context_characters": 4000,
                "truncated": False,
                "sources": sources,
            },
        ) as context:
            result = service.ask(
                query="explain",
                max_context_characters=16000,
                max_output_tokens=3000,
            )

        self.assertEqual(
            context.call_args.kwargs["max_context_characters"], 4000
        )
        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(len(generator.calls[1]["sources"]), 2)
        self.assertEqual(result["context"]["generation_attempts"], 2)
        self.assertTrue(result["context"]["reduced_for_generation"])
        self.assertEqual(result["context"]["context_characters"], 2000)
        self.assertEqual(generator.calls[-1]["max_output_tokens"], 2048)
        self.assertEqual(result["context"]["requested_max_output_tokens"], 3000)
        self.assertEqual(result["context"]["max_output_tokens"], 2048)
        self.assertEqual(
            result["context"]["requested_max_context_characters"], 16000
        )

    def test_ask_reduces_large_output_reservation_before_evidence(self) -> None:
        generator = _RetryGenerator()
        service = api.RagApiService(
            self.settings(),
            generator=generator,
            generation_config=GenerationConfig(
                path=Path("generation.toml"),
                base_url="http://127.0.0.1:8000/v1",
                model="local-test-model",
                max_output_tokens=4096,
                max_context_characters=8000,
            ),
        )
        sources = [
            {
                "source_id": "S1",
                "project": "Solver",
                "selected_occurrence": {
                    "branch": "trunk",
                    "commit_sha": "a" * 40,
                },
                "path": "src/flow.cpp",
                "text": "flow evidence" * 200,
            }
        ]
        with mock.patch.object(
            service,
            "context",
            return_value={
                "query": "explain the flow",
                "mode": "hybrid",
                "instructions": api.CONTEXT_INSTRUCTIONS,
                "retrieved_count": 1,
                "source_count": 1,
                "context_characters": 2600,
                "max_context_characters": 8000,
                "truncated": False,
                "sources": sources,
            },
        ):
            result = service.ask(query="explain the flow")

        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(generator.calls[0]["max_output_tokens"], 4096)
        self.assertEqual(generator.calls[1]["max_output_tokens"], 2048)
        self.assertEqual(generator.calls[1]["sources"], sources)
        self.assertFalse(result["context"]["reduced_for_generation"])
        self.assertTrue(result["context"]["reduced_output_for_generation"])
        self.assertEqual(result["context"]["context_characters"], 2600)
        self.assertEqual(result["context"]["max_output_tokens"], 2048)


if __name__ == "__main__":
    unittest.main()
