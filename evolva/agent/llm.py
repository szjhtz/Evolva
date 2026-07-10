from __future__ import annotations

import json
import socket
import time
import random
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from evolva.config import AgentConfig


@dataclass
class LLMResponse:
    content: str
    raw: dict[str, Any] | None = None
    attempts: int = 1
    retries: int = 0
    usage: dict[str, Any] | None = None
    request_id: str = ""
    model: str = ""
    finish_reason: str = ""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("LLM request cancelled")


class OpenAICompatibleLLM:
    """Minimal OpenAI-compatible chat client using stdlib only."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self._response_state = threading.local()

    @property
    def available(self) -> bool:
        return bool(self.config.api_key)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        timeout: int | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> LLMResponse:
        if not self.available:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
        }
        resolved_temperature = self.config.temperature if temperature is None else temperature
        if resolved_temperature is not None:
            payload["temperature"] = resolved_temperature
        request_timeout = int(timeout) if timeout is not None else int(self.config.request_timeout)
        attempts = max(1, 1 + int(getattr(self.config, "llm_max_retries", 0)))
        for attempt in range(attempts):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            try:
                raw = self._post_chat(payload, request_timeout)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if payload.get("temperature") is not None and self._temperature_must_be_default(exc.code, body):
                    retry_payload = dict(payload)
                    retry_payload.pop("temperature", None)
                    try:
                        raw = self._post_chat(retry_payload, request_timeout)
                    except urllib.error.HTTPError as retry_exc:
                        retry_body = retry_exc.read().decode("utf-8", errors="replace")
                        raise RuntimeError(f"LLM HTTP {retry_exc.code}: {retry_body}") from retry_exc
                    except Exception:
                        raise
                    return self._response(raw, attempts=attempt + 2, retries=attempt + 1)
                if self._is_retryable_http(exc.code) and attempt + 1 < attempts:
                    self._sleep_before_retry(attempt, self._retry_after(exc), cancellation_token)
                    continue
                raise RuntimeError(f"LLM HTTP {exc.code}: {body}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                if attempt + 1 < attempts:
                    self._sleep_before_retry(attempt, cancellation_token=cancellation_token)
                    continue
                raise RuntimeError(f"LLM request failed after {attempts} attempt(s): {exc}") from exc
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            return self._response(raw, attempts=attempt + 1, retries=attempt)
        raise RuntimeError(f"LLM request failed after {attempts} attempt(s)")

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        required_keys: list[str] | None = None,
        schema: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        attempts = max(1, 1 + int(getattr(self.config, "llm_structured_retries", 1)))
        repair_messages = list(messages)
        last_error = "LLM response did not contain a JSON object"
        for attempt in range(attempts):
            response = self.chat(repair_messages, **kwargs)
            data = extract_json_object(response.content)
            if data is not None:
                errors = validate_json_object(data, required_keys=required_keys, schema=schema)
                if not errors:
                    return data
                last_error = "; ".join(errors)
            if attempt + 1 >= attempts:
                break
            repair_messages = [
                *messages,
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": (
                        "Return only one corrected JSON object. Do not add markdown. "
                        f"Validation error: {last_error}"
                    ),
                },
            ]
            kwargs = {**kwargs, "temperature": 0.0}
        raise RuntimeError(last_error)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        on_chunk: Callable[[str], None] | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> LLMResponse:
        """Stream a plain chat completion through an OpenAI-compatible SSE API."""

        if not self.available:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        resolved_temperature = self.config.temperature if temperature is None else temperature
        if resolved_temperature is not None:
            payload["temperature"] = resolved_temperature
        request_timeout = int(timeout) if timeout is not None else int(self.config.request_timeout)
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        chunks: list[str] = []
        usage: dict[str, Any] | None = None
        model = self.config.model
        finish_reason = ""
        consumed = 0
        limit = max(1024, int(getattr(self.config, "llm_max_response_bytes", 10_000_000)))
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        try:
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                headers = getattr(resp, "headers", {})
                response_headers = {str(key).lower(): str(value) for key, value in getattr(headers, "items", lambda: [])()}
                request_id = str(response_headers.get("x-request-id") or "")
                for raw_line in resp:
                    if cancellation_token is not None:
                        cancellation_token.raise_if_cancelled()
                    consumed += len(raw_line)
                    if consumed > limit:
                        raise RuntimeError(f"LLM streaming response exceeded {limit} bytes")
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    if isinstance(event.get("usage"), dict):
                        usage = event["usage"]
                    model = str(event.get("model") or model)
                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    finish_reason = str(choice.get("finish_reason") or finish_reason)
                    raw_delta = choice.get("delta")
                    delta: dict[str, Any] = dict(raw_delta) if isinstance(raw_delta, dict) else {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        chunks.append(content)
                        if on_chunk is not None:
                            on_chunk(content)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise RuntimeError(f"LLM streaming request failed: {exc}") from exc
        return LLMResponse(
            content="".join(chunks),
            attempts=1,
            retries=0,
            usage=usage,
            request_id=request_id,
            model=model,
            finish_reason=finish_reason,
        )

    def _post_chat(self, payload: dict[str, Any], request_timeout: int) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=request_timeout) as resp:
            headers = getattr(resp, "headers", {})
            self._response_state.headers = {str(key).lower(): str(value) for key, value in getattr(headers, "items", lambda: [])()}
            limit = max(1024, int(getattr(self.config, "llm_max_response_bytes", 10_000_000)))
            try:
                body = resp.read(limit + 1)
            except TypeError:  # Compatibility with small test/provider response shims.
                body = resp.read()
            if len(body) > limit:
                raise RuntimeError(f"LLM response exceeded {limit} bytes")
            return json.loads(body.decode("utf-8"))

    def _content_from_raw(self, raw: dict[str, Any]) -> str:
        try:
            return str(raw["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM response missing choices[0].message.content") from exc

    def _response(self, raw: dict[str, Any], *, attempts: int, retries: int) -> LLMResponse:
        choice = raw.get("choices", [{}])[0] if isinstance(raw.get("choices"), list) and raw.get("choices") else {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        headers = getattr(self._response_state, "headers", {})
        request_id = str(headers.get("x-request-id") or raw.get("id") or "")
        return LLMResponse(
            content=self._content_from_raw(raw),
            raw=raw,
            attempts=attempts,
            retries=retries,
            usage=usage,
            request_id=request_id,
            model=str(raw.get("model") or self.config.model),
            finish_reason=str(choice.get("finish_reason") or "") if isinstance(choice, dict) else "",
        )

    def _sleep_before_retry(
        self,
        attempt: int,
        retry_after: float | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        backoff = max(0.0, float(getattr(self.config, "llm_retry_backoff", 0.25)))
        jitter = max(0.0, float(getattr(self.config, "llm_retry_jitter", 0.1)))
        delay = retry_after if retry_after is not None else backoff * (2**attempt)
        if delay > 0:
            remaining = min(delay + random.uniform(0.0, jitter), 30.0)
            while remaining > 0:
                if cancellation_token is not None:
                    cancellation_token.raise_if_cancelled()
                interval = min(0.1, remaining)
                time.sleep(interval)
                remaining -= interval

    @staticmethod
    def _retry_after(exc: urllib.error.HTTPError) -> float | None:
        value = getattr(exc, "headers", {}).get("Retry-After") if getattr(exc, "headers", None) else None
        try:
            return max(0.0, float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_retryable_http(status_code: int) -> bool:
        return status_code in {408, 429, 500, 502, 503, 504}

    @staticmethod
    def _temperature_must_be_default(status_code: int, body: str) -> bool:
        if status_code != 400:
            return False
        lowered = body.lower()
        return "temperature" in lowered and ("unsupported" in lowered or "default" in lowered)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model response."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                text = candidate
                break
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text[start:], start=start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def validate_json_object(
    data: dict[str, Any],
    *,
    required_keys: list[str] | None = None,
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """Validate the useful subset of JSON Schema needed by agent actions."""

    errors: list[str] = []
    missing = [key for key in required_keys or [] if key not in data]
    if missing:
        errors.append(f"LLM JSON response missing required keys: {', '.join(missing)}")
    if schema:
        _validate_schema_value(data, schema, "$", errors)
    return errors


def _validate_schema_value(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    type_map: dict[str, type[Any] | tuple[type[Any], ...]] = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    expected = type_map.get(str(expected_type)) if expected_type else None
    if expected is not None and (not isinstance(value, expected) or expected_type in {"number", "integer"} and isinstance(value, bool)):
        errors.append(f"{path} must be {expected_type}")
        return
    if "enum" in schema and value not in schema.get("enum", []):
        errors.append(f"{path} must be one of {schema['enum']}")
    if isinstance(value, dict):
        required = [str(key) for key in schema.get("required", [])]
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key} is required")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    _validate_schema_value(value[key], child_schema, f"{path}.{key}", errors)
            if schema.get("additionalProperties") is False:
                extras = sorted(str(key) for key in value if key not in properties)
                if extras:
                    errors.append(f"{path} has unsupported properties: {', '.join(extras)}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_schema_value(item, schema["items"], f"{path}[{index}]", errors)
