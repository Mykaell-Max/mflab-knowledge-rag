from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from mflab_knowledge import api
from mflab_knowledge.generation import (
    GenerationConfig,
    GenerationContextTooLargeError,
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


class ApiServiceTests(unittest.TestCase):
    def settings(self) -> api.ApiSettings:
        return api.ApiSettings(
            database_url="postgresql://secret@example/test",
            state_dir=Path("missing-state-for-test"),
        )

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

        self.assertEqual(context["source_count"], 1)
        self.assertEqual(context["context_characters"], 700)
        self.assertTrue(context["truncated"])
        source = context["sources"][0]
        self.assertEqual(source["source_id"], "S1")
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
            )

        self.assertEqual(
            context.call_args.kwargs["max_context_characters"], 4000
        )
        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(len(generator.calls[1]["sources"]), 1)
        self.assertEqual(result["context"]["generation_attempts"], 2)
        self.assertTrue(result["context"]["reduced_for_generation"])
        self.assertEqual(result["context"]["context_characters"], 2000)
        self.assertEqual(
            result["context"]["requested_max_context_characters"], 16000
        )


if __name__ == "__main__":
    unittest.main()
