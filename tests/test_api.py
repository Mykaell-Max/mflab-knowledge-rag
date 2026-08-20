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


class _PlanningGenerator(_Generator):
    def __init__(self) -> None:
        super().__init__("The call flow is established [S1].")
        self.plan_calls: list[dict[str, object]] = []

    def plan_retrieval(self, **kwargs: object) -> str:
        self.plan_calls.append(kwargs)
        return (
            '{"queries":["mesh creation initialization call flow"],'
            '"identifiers":["MeshFactory","initialize"]}'
        )


class _InvestigatingGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def investigate(self, **_kwargs: object) -> str:
        self.calls += 1
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


class ApiServiceTests(unittest.TestCase):
    def settings(self) -> api.ApiSettings:
        return api.ApiSettings(
            database_url="postgresql://secret@example/test",
            state_dir=Path("missing-state-for-test"),
            generation_config=Path("missing-generation-for-test.toml"),
        )

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
            ["manager-init", "domain", "state", "factory", "manager-run", "config"],
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
                    },
                    progress_callback=progress.append,
                )

        self.assertEqual(investigator.calls, 3)
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

        self.assertEqual(len(investigator.calls), 5)
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
            [
                item["path"]
                for item in context["agent_investigation"]["graph_frontier"]
            ],
            ["src/driver.cpp", "src/helper.cpp"],
        )

    def test_ask_audits_long_answers_in_bounded_batches(self) -> None:
        answer = "\n\n".join(
            f"Claim {position} is supported [S1]." for position in range(1, 8)
        )
        audits = []
        for identifiers in ((1, 2, 3), (4, 5, 6), (7,)):
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
        self.assertEqual(result["verification"]["batches"], 3)
        self.assertEqual(result["verification"]["counts"]["supported"], 7)
        self.assertEqual(len(generator.verify_calls), 3)

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
        self.assertEqual(len(generator.verify_calls), 2)

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
        self.assertEqual(len(generator.verify_calls), 3)
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
        self.assertEqual(len(generator.verify_calls), 2)
        self.assertIn(
            "Afirmações rejeitadas removidas",
            [step["title"] for step in progress],
        )

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


if __name__ == "__main__":
    unittest.main()
