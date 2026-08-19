from __future__ import annotations

import json
import os
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

GENERATION_CONFIG_SCHEMA_VERSION = "0.1"
MAX_GENERATION_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_GENERATION_ERROR_BYTES = 64 * 1024


class GenerationNotConfiguredError(RuntimeError):
    """Raised when /ask is called before a local provider is configured."""


class GenerationUnavailableError(RuntimeError):
    """Raised when the configured local generation server cannot answer."""


class GenerationContextTooLargeError(GenerationUnavailableError):
    """Raised when the local provider rejects an oversized prompt."""


@dataclass(frozen=True)
class GenerationConfig:
    path: Path
    base_url: str
    model: str
    timeout_seconds: int = 180
    max_output_tokens: int = 1024
    temperature: float = 0.1
    max_context_characters: int = 8000

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def _validate_local_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("generation base_url deve usar http:// ou https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("generation base_url não aceita credenciais, query ou fragmento")
    if parsed.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError(
            "generation base_url deve apontar diretamente para 127.0.0.1 ou ::1"
        )
    if not parsed.port:
        raise ValueError("generation base_url exige uma porta explícita")
    return value.strip().rstrip("/")


def load_generation_config(
    path: Path,
    *,
    optional: bool = False,
) -> GenerationConfig | None:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        if optional:
            return None
        raise ValueError(f"configuração de geração não encontrada: {resolved}")
    try:
        value = tomllib.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"configuração de geração inválida: {resolved}: {exc}") from exc
    if value.get("schema_version") != GENERATION_CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"generation schema_version deve ser {GENERATION_CONFIG_SCHEMA_VERSION}"
        )
    unknown_top = set(value) - {"schema_version", "provider"}
    if unknown_top:
        raise ValueError(
            f"opções desconhecidas em generation.toml: {', '.join(sorted(unknown_top))}"
        )
    provider = value.get("provider")
    if not isinstance(provider, dict):
        raise ValueError("generation.toml exige a tabela [provider]")
    allowed_provider = {
        "kind",
        "base_url",
        "model",
        "timeout_seconds",
        "max_output_tokens",
        "temperature",
        "max_context_characters",
    }
    unknown_provider = set(provider) - allowed_provider
    if unknown_provider:
        raise ValueError(
            "opções desconhecidas em [provider]: "
            + ", ".join(sorted(unknown_provider))
        )
    if provider.get("kind") != "openai_compatible":
        raise ValueError("provider.kind deve ser openai_compatible")
    base_url = provider.get("base_url")
    model = provider.get("model")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("provider.base_url é obrigatório")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("provider.model é obrigatório")
    timeout_seconds = provider.get("timeout_seconds", 180)
    max_output_tokens = provider.get("max_output_tokens", 1024)
    temperature = provider.get("temperature", 0.1)
    max_context_characters = provider.get("max_context_characters", 8000)
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 900:
        raise ValueError("provider.timeout_seconds deve estar entre 1 e 900")
    if not isinstance(max_output_tokens, int) or not 64 <= max_output_tokens <= 8192:
        raise ValueError("provider.max_output_tokens deve estar entre 64 e 8192")
    if (
        not isinstance(temperature, (int, float))
        or isinstance(temperature, bool)
        or not 0 <= float(temperature) <= 1
    ):
        raise ValueError("provider.temperature deve estar entre 0 e 1")
    if (
        not isinstance(max_context_characters, int)
        or isinstance(max_context_characters, bool)
        or not 1000 <= max_context_characters <= 100000
    ):
        raise ValueError(
            "provider.max_context_characters deve estar entre 1000 e 100000"
        )
    return GenerationConfig(
        path=resolved,
        base_url=_validate_local_base_url(base_url),
        model=model.strip(),
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
        temperature=float(temperature),
        max_context_characters=max_context_characters,
    )


def _is_context_length_error(error: urllib.error.HTTPError) -> bool:
    if error.code not in {400, 413, 422}:
        return False
    try:
        raw = error.read(MAX_GENERATION_ERROR_BYTES + 1)
    except OSError:
        return False
    finally:
        error.close()
    if len(raw) > MAX_GENERATION_ERROR_BYTES:
        return False
    text = raw.decode("utf-8", errors="replace").casefold()
    markers = (
        "maximum context length",
        "context length",
        "max_model_len",
        "too many tokens",
        "input is too long",
        "prompt is too long",
    )
    return any(marker in text for marker in markers)


def load_generation_api_key(env_file: Path) -> str | None:
    key = os.environ.get("MFLAB_LLM_API_KEY", "").strip()
    if key:
        return _validate_api_key(key)
    path = env_file.expanduser().resolve()
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"não foi possível ler {path}") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == "MFLAB_LLM_API_KEY":
            result = value.strip()
            if len(result) >= 2 and result[0] == result[-1] and result[0] in {'"', "'"}:
                result = result[1:-1]
            return _validate_api_key(result) if result else None
    return None


def _validate_api_key(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("MFLAB_LLM_API_KEY contém quebra de linha inválida")
    return value


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


class OpenAICompatibleGenerator:
    def __init__(
        self,
        config: GenerationConfig,
        *,
        api_key: str | None = None,
        opener: Callable[..., object] | None = None,
    ) -> None:
        self.config = config
        self.api_key = api_key
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RejectRedirects(),
        ).open

    def generate(
        self,
        *,
        question: str,
        instructions: str,
        sources: list[dict[str, object]],
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, object]:
        selected_tokens = (
            self.config.max_output_tokens
            if max_output_tokens is None
            else max_output_tokens
        )
        selected_temperature = (
            self.config.temperature if temperature is None else temperature
        )
        if selected_tokens < 64 or selected_tokens > 8192:
            raise ValueError("max_output_tokens deve estar entre 64 e 8192")
        if selected_temperature < 0 or selected_temperature > 1:
            raise ValueError("temperature deve estar entre 0 e 1")
        evidence = json.dumps(sources, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": (
                        f"Question:\n{question}\n\n"
                        "Indexed evidence as JSON:\n"
                        f"{evidence}"
                    ),
                },
            ],
            "temperature": selected_temperature,
            "max_tokens": selected_tokens,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            response = self._opener(request, timeout=self.config.timeout_seconds)
            with response:
                raw = response.read(MAX_GENERATION_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if _is_context_length_error(exc):
                raise GenerationContextTooLargeError(
                    "contexto excedeu a janela do gerador local"
                ) from exc
            raise GenerationUnavailableError(
                f"servidor local de geração respondeu HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GenerationUnavailableError(
                "servidor local de geração indisponível"
            ) from exc
        if len(raw) > MAX_GENERATION_RESPONSE_BYTES:
            raise GenerationUnavailableError("resposta do gerador excedeu 4 MiB")
        try:
            value = json.loads(raw.decode("utf-8"))
            choice = value["choices"][0]
            answer = choice["message"]["content"]
        except (UnicodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise GenerationUnavailableError(
                "resposta inválida do servidor local de geração"
            ) from exc
        if not isinstance(answer, str) or not answer.strip():
            raise GenerationUnavailableError("servidor local retornou resposta vazia")
        usage = value.get("usage")
        return {
            "answer": answer.strip(),
            "model": str(value.get("model") or self.config.model),
            "finish_reason": choice.get("finish_reason"),
            "usage": usage if isinstance(usage, dict) else None,
        }
