"""Provider-agnostic chat completion used for extraction and digest narratives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..config import Settings, get_api_key


@dataclass(frozen=True)
class LlmResponse:
    text: str
    provider: str
    model: str


class LlmProvider(Protocol):
    name: str
    model: str

    def complete(
        self, system: str, user: str, json_mode: bool = False, max_tokens: int = 2048
    ) -> LlmResponse: ...


class OpenAIChat:
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None, client=None) -> None:
        self.model = model
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key or get_api_key("openai"))
        self.client = client

    def complete(
        self, system: str, user: str, json_mode: bool = False, max_tokens: int = 2048
    ) -> LlmResponse:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
            max_tokens=max_tokens,
            **kwargs,
        )
        return LlmResponse(resp.choices[0].message.content or "", self.name, self.model)


class AnthropicChat:
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, client=None) -> None:
        self.model = model
        if client is None:
            from anthropic import Anthropic

            client = Anthropic(api_key=api_key or get_api_key("anthropic"))
        self.client = client

    def complete(
        self, system: str, user: str, json_mode: bool = False, max_tokens: int = 2048
    ) -> LlmResponse:
        if json_mode:
            system = system + "\nRespond with a single JSON object and nothing else."
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(block, "text", "") for block in resp.content)
        return LlmResponse(text, self.name, self.model)


def make_llm(
    settings: Settings, provider: str | None = None, model: str | None = None
) -> LlmProvider:
    name = provider or settings.llm.provider
    chosen = model or settings.llm.model
    if name == "openai":
        return OpenAIChat(chosen)
    if name == "anthropic":
        return AnthropicChat(chosen)
    raise ValueError(f"unknown LLM provider: {name}")
