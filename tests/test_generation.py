from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from mflab_knowledge import generation


class _Response:
    def __init__(self, value: dict[str, object]) -> None:
        self.raw = json.dumps(value).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return self.raw


class GenerationTests(unittest.TestCase):
    def _write_config(self, root: Path, base_url: str) -> Path:
        path = root / "generation.toml"
        path.write_text(
            "\n".join(
                [
                    'schema_version = "0.1"',
                    "",
                    "[provider]",
                    'kind = "openai_compatible"',
                    f'base_url = "{base_url}"',
                    'model = "local-test-model"',
                    "timeout_seconds = 30",
                    "max_output_tokens = 256",
                    "temperature = 0.2",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def test_loads_generic_local_provider_and_rejects_external_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = generation.load_generation_config(
                self._write_config(root, "http://127.0.0.1:8000/v1")
            )
            assert config is not None
            self.assertEqual(
                config.endpoint,
                "http://127.0.0.1:8000/v1/chat/completions",
            )
            self.assertEqual(config.model, "local-test-model")
            self.assertEqual(config.max_context_characters, 8000)
            self.assertTrue(config.verify_evidence)
            self.assertEqual(config.verification_max_attempts, 2)
            self.assertEqual(config.max_repair_attempts, 1)

            with self.assertRaisesRegex(ValueError, "127.0.0.1"):
                generation.load_generation_config(
                    self._write_config(root, "https://llm.example/v1")
                )

    def test_unknown_options_are_rejected_instead_of_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path = self._write_config(root, "http://127.0.0.1:8000/v1")
            path.write_text(
                path.read_text(encoding="utf-8") + "\nrepository = \"fixed\"\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "opções desconhecidas"):
                generation.load_generation_config(path)

    def test_api_key_is_read_without_entering_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text(
                'MFLAB_LLM_API_KEY="local-secret"\n',
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {}, clear=True):
                key = generation.load_generation_api_key(env_file)

        self.assertEqual(key, "local-secret")

    def test_posts_openai_compatible_request_and_parses_response(self) -> None:
        config = generation.GenerationConfig(
            path=Path("generation.toml"),
            base_url="http://127.0.0.1:8000/v1",
            model="local-test-model",
        )
        captured: dict[str, object] = {}

        def opener(request: object, *, timeout: int) -> _Response:
            captured["request"] = request
            captured["timeout"] = timeout
            return _Response(
                {
                    "model": "served-model",
                    "choices": [
                        {
                            "message": {"content": "Resposta baseada em [S1]."},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"total_tokens": 42},
                }
            )

        generator = generation.OpenAICompatibleGenerator(
            config,
            api_key="secret-never-returned",
            opener=opener,
        )
        result = generator.generate(
            question="Como funciona?",
            instructions="Cite fontes.",
            sources=[{"source_id": "S1", "text": "evidência"}],
            max_output_tokens=128,
        )

        request = captured["request"]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, config.endpoint)
        self.assertEqual(captured["timeout"], 180)
        self.assertEqual(payload["model"], "local-test-model")
        self.assertEqual(payload["max_tokens"], 128)
        self.assertFalse(payload["stream"])
        self.assertIn("S1", payload["messages"][1]["content"])
        self.assertEqual(result["answer"], "Resposta baseada em [S1].")
        self.assertEqual(result["model"], "served-model")
        self.assertNotIn("secret-never-returned", str(result))

    def test_identifies_provider_context_limit_without_exposing_body(self) -> None:
        config = generation.GenerationConfig(
            path=Path("generation.toml"),
            base_url="http://127.0.0.1:8000/v1",
            model="local-test-model",
        )

        def opener(request: object, *, timeout: int) -> object:
            del request, timeout
            raise urllib.error.HTTPError(
                config.endpoint,
                400,
                "Bad Request",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"error":{"message":"maximum context length exceeded"}}'
                ),
            )

        generator = generation.OpenAICompatibleGenerator(config, opener=opener)
        with self.assertRaisesRegex(
            generation.GenerationContextTooLargeError,
            "janela",
        ):
            generator.generate(
                question="question",
                instructions="instructions",
                sources=[{"source_id": "S1", "text": "evidence"}],
            )

    def test_verification_requests_structured_local_evidence_audit(self) -> None:
        config = generation.GenerationConfig(
            path=Path("generation.toml"),
            base_url="http://127.0.0.1:8000/v1",
            model="local-test-model",
        )
        captured: dict[str, object] = {}

        def opener(request: object, *, timeout: int) -> _Response:
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"claims":[{"claim_id":"C1","verdict":"unsupported","source_ids":["S1"],"finding":"Only a local pointer is assigned."}]}'
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            )

        generator = generation.OpenAICompatibleGenerator(config, opener=opener)
        result = generator.verify(
            question="Where is the mesh initialized?",
            answer="It is initialized here [S1].",
            claims=[{"claim_id": "C1", "text": "It is initialized here [S1]."}],
            sources=[{"source_id": "S1", "text": "mesh = _mesh;"}],
        )

        payload = captured["payload"]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["temperature"], 0.0)
        self.assertIn("unsupported", result)


if __name__ == "__main__":
    unittest.main()
