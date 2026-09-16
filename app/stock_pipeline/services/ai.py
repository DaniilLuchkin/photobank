import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..config import Settings


class AIProvider(Protocol):
    name: str
    model: str

    def generate(self, *, asset_name: str, media_type: str, analysis: dict[str, Any], frames: list[str]) -> dict[str, Any]: ...


def _base_metadata(asset_name: str, media_type: str, analysis: dict[str, Any]) -> dict[str, Any]:
    stem = Path(asset_name).stem.replace("_", " ").replace("-", " ").strip()
    title = stem or ("Stock photo" if media_type == "photo" else "Stock video")
    dimensions = "x".join(str(analysis[key]) for key in ("width", "height") if analysis.get(key))
    return {
        "title": title[:150],
        "description": title,
        "keywords": [word.lower() for word in title.split() if len(word) > 2][:20],
        "categories": [],
        "location": {"value": None, "source": None, "confidence": 0},
        "content_type": "commercial",
        "editorial_reason": None,
        "release_hints": [],
        "policy_flags": [],
        "confidence": 0.2,
        "warnings": ["Mock metadata: review before submission", f"Detected dimensions: {dimensions}"],
    }


class MockAIProvider:
    name = "mock"
    model = "mock-v1"

    def generate(self, *, asset_name: str, media_type: str, analysis: dict[str, Any], frames: list[str]) -> dict[str, Any]:
        return _base_metadata(asset_name, media_type, analysis)


class OpenAICompatibleProvider:
    def __init__(self, settings: Settings, provider_name: str, api_key: str, default_base_url: str) -> None:
        self.name = provider_name
        self.model = settings.ai_model or "gpt-4.1-mini"
        self.api_key = api_key
        self.base_url = (settings.ai_base_url or default_base_url).rstrip("/")

    def generate(self, *, asset_name: str, media_type: str, analysis: dict[str, Any], frames: list[str]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": self._prompt(asset_name, media_type, analysis)}]
        for frame in frames[:12]:
            path = Path(frame)
            if not path.exists():
                continue
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": content}],
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        return json.loads(payload["choices"][0]["message"]["content"])

    @staticmethod
    def _prompt(asset_name: str, media_type: str, analysis: dict[str, Any]) -> str:
        return (
            "Describe ONLY what is visibly supported by the supplied media. Do not invent location, species, brands, "
            "people, activities or legal safety. Return JSON with title, description, keywords, categories, location, "
            "content_type, release_hints, policy_flags, confidence and warnings. Remove duplicate keywords and avoid "
            f"keyword stuffing. Filename={asset_name}; media_type={media_type}; technical_analysis={analysis}"
        )


def build_ai_provider(settings: Settings) -> AIProvider:
    if settings.ai_provider == "openai" and settings.openai_api_key:
        return OpenAICompatibleProvider(settings, "openai", settings.openai_api_key, "https://api.openai.com/v1")
    if settings.ai_provider == "openrouter" and settings.openrouter_api_key:
        return OpenAICompatibleProvider(settings, "openrouter", settings.openrouter_api_key, "https://openrouter.ai/api/v1")
    return MockAIProvider()


def input_fingerprint(asset_sha256: str, model: str, prompt_version: str) -> str:
    return hashlib.sha256(f"{asset_sha256}:{model}:{prompt_version}".encode()).hexdigest()

