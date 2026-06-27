"""Ollama HTTP adapter for Phase C patch collection."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from earnbench.agents.base import (
    AgentRunContext,
    AgentRunResult,
    BaseAgentAdapter,
    extract_unified_diff,
)
from earnbench.agents.schemas import AgentArmSpec


class OllamaAdapter(BaseAgentAdapter):
    """Collect patches via the local Ollama HTTP API."""

    provider = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 600.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def run(self, context: AgentRunContext) -> AgentRunResult:
        model = context.arm.model
        self.ensure_model_available(model)

        payload = {
            "model": model,
            "prompt": context.prompt,
            "stream": False,
            "options": {
                "temperature": context.arm.temperature,
                "seed": context.seed,
            },
        }
        response_text, raw_payload = self._post_json("/api/generate", payload)
        trajectory = json.dumps(
            {
                "endpoint": f"{self.base_url}/api/generate",
                "model": model,
                "seed": context.seed,
                "temperature": context.arm.temperature,
                "request": payload,
                "response": raw_payload,
            },
            indent=2,
            sort_keys=True,
        )
        patch = extract_unified_diff(response_text)
        if patch is None:
            return AgentRunResult(
                trajectory_text=trajectory + "\n",
                patch_text="",
                status="no_patch",
                error="no valid unified diff found in model output",
                model=model,
            )
        return AgentRunResult(
            trajectory_text=trajectory + "\n",
            patch_text=patch,
            status="ok",
            model=model,
        )

    def ensure_model_available(self, model: str) -> None:
        """Raise ``RuntimeError`` when ``model`` is not listed by Ollama."""
        tags_payload = self._get_json("/api/tags")
        models = tags_payload.get("models") or []
        available = {
            str(item.get("name", ""))
            for item in models
            if isinstance(item, dict)
        }
        if model not in available:
            known = ", ".join(sorted(name for name in available if name))
            msg = (
                f"ollama model {model!r} is not available at {self.base_url}; "
                f"known models: {known or '(none)'}"
            )
            raise RuntimeError(msg)

    def _get_json(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            msg = f"ollama request failed for {url}: {exc}"
            raise RuntimeError(msg) from exc
        if not isinstance(payload, dict):
            msg = f"ollama response must be a JSON object: {url}"
            raise RuntimeError(msg)
        return payload

    def _post_json(self, path: str, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            msg = f"ollama request failed for {url}: {exc}"
            raise RuntimeError(msg) from exc
        if not isinstance(payload, dict):
            msg = f"ollama response must be a JSON object: {url}"
            raise RuntimeError(msg)
        response_text = str(payload.get("response") or "")
        return response_text, payload


def build_ollama_adapter(arm: AgentArmSpec) -> OllamaAdapter:
    """Return an Ollama adapter configured from an arm spec."""
    return OllamaAdapter(base_url=arm.base_url)
