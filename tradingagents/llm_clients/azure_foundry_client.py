"""Azure AI Foundry meta-client with per-model path routing.

A single Azure AI Foundry resource exposes several models through *different*
wire protocols on the same host:

    - OpenAI-compatible  : ``/openai/v1/``            (DeepSeek, Grok, Llama, ...)
    - Anthropic          : ``/anthropic/v1/messages`` (Claude family)

and GPT models live on a *separate* Azure OpenAI resource (its own key). One
``azure-foundry`` provider therefore can't map to one wire client — instead this
class inspects the model id and delegates to the right existing client:

    claude-*            -> AnthropicClient   (base_url = AZURE_FOUNDRY_ANTHROPIC_ENDPOINT)
    gpt-* / o1/o3/o4-*  -> AzureOpenAIClient  (separate Azure OpenAI resource, AZURE_OPENAI_*)
    everything else     -> ChatOpenAI         (base_url = AZURE_FOUNDRY_OPENAI_ENDPOINT)

The OpenAI-compatible path reuses ``NormalizedChatOpenAI`` /
``DeepSeekChatOpenAI`` so the per-model capability table
(``capabilities.get_capabilities``) still governs structured-output method and
``tool_choice`` suppression — which is why the capitalized Azure deployment names
(``DeepSeek-V4-Pro``) must be registered there.

Environment variables (one Foundry key shared across Claude/DeepSeek/Grok):

    AZURE_FOUNDRY_API_KEY            shared key for the Foundry resource
    AZURE_FOUNDRY_OPENAI_ENDPOINT   e.g. https://<res>.services.ai.azure.com/openai/v1/
    AZURE_FOUNDRY_ANTHROPIC_ENDPOINT e.g. https://<res>.services.ai.azure.com/anthropic

GPT models additionally use the existing Azure OpenAI variables
(``AZURE_OPENAI_API_KEY`` / ``AZURE_OPENAI_ENDPOINT`` / ``OPENAI_API_VERSION`` /
``AZURE_OPENAI_DEPLOYMENT_NAME``).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .base_client import BaseLLMClient

# kwargs we forward to the OpenAI-compatible chat class (others are dropped so a
# provider-specific kwarg never reaches a model that rejects it).
_OPENAI_COMPAT_PASSTHROUGH = (
    "timeout", "max_retries", "temperature",
    "callbacks", "http_client", "http_async_client",
)

_AZURE_OPENAI_PREFIXES = ("gpt", "o1", "o3", "o4")


class AzureFoundryClient(BaseLLMClient):
    """Route an Azure AI Foundry model id to the correct wire client."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, **kwargs)
        self.provider = "azure-foundry"

    # -- routing ---------------------------------------------------------------
    def _route(self) -> str:
        m = self.model.lower()
        if m.startswith("claude"):
            return "anthropic"
        if m.startswith(_AZURE_OPENAI_PREFIXES):
            return "azure_openai"
        return "openai_compatible"

    @staticmethod
    def _require_env(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise ValueError(
                f"{name} is not set. Add it to your .env (see "
                f"niranjan_docs/credentials-setup.md) to use azure-foundry models."
            )
        return value

    # -- build -----------------------------------------------------------------
    def get_llm(self) -> Any:
        route = self._route()
        if route == "anthropic":
            return self._build_anthropic()
        if route == "azure_openai":
            return self._build_azure_openai()
        return self._build_openai_compatible()

    def _build_anthropic(self) -> Any:
        from .anthropic_client import AnthropicClient

        endpoint = self._require_env("AZURE_FOUNDRY_ANTHROPIC_ENDPOINT")
        key = self._require_env("AZURE_FOUNDRY_API_KEY")
        kwargs = dict(self.kwargs)
        kwargs["api_key"] = key
        # AnthropicClient consults the same capability/effort logic and wraps in
        # NormalizedChatAnthropic; base_url points it at the Foundry endpoint.
        return AnthropicClient(self.model, base_url=endpoint, **kwargs).get_llm()

    def _build_azure_openai(self) -> Any:
        from .azure_client import AzureOpenAIClient

        # GPT roles use a SEPARATE Azure OpenAI resource/key from the Foundry key.
        # Preflight those vars here so a role map containing a GPT model fails at
        # graph-build time with a clear message, rather than late on first call.
        self._require_env("AZURE_OPENAI_API_KEY")
        self._require_env("AZURE_OPENAI_ENDPOINT")
        return AzureOpenAIClient(self.model, **self.kwargs).get_llm()

    def _build_openai_compatible(self) -> Any:
        from .openai_client import DeepSeekChatOpenAI, NormalizedChatOpenAI

        endpoint = self._require_env("AZURE_FOUNDRY_OPENAI_ENDPOINT")
        key = self._require_env("AZURE_FOUNDRY_API_KEY")

        llm_kwargs = {"model": self.model, "base_url": endpoint, "api_key": key}
        for k in _OPENAI_COMPAT_PASSTHROUGH:
            if k in self.kwargs:
                llm_kwargs[k] = self.kwargs[k]

        # DeepSeek V4 needs the reasoning_content round-trip + tool_choice
        # suppression handled by its subclass; other catalog models use the base.
        chat_cls = (
            DeepSeekChatOpenAI if "deepseek" in self.model.lower()
            else NormalizedChatOpenAI
        )
        return chat_cls(**llm_kwargs)

    def validate_model(self) -> bool:
        # Foundry serves a large, fast-moving catalog; accept any deployed id
        # (the underlying client still warns via its own validation where it has
        # a known-model list).
        return True
