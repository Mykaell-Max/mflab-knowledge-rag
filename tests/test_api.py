from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from mflab_knowledge import api


class _Embedder:
    profile_id = "test-profile"


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

    def test_non_loopback_host_is_rejected_before_importing_server(self) -> None:
        with self.assertRaisesRegex(ValueError, "somente um endereço loopback"):
            api.run_api(self.settings(), host="0.0.0.0")


if __name__ == "__main__":
    unittest.main()
