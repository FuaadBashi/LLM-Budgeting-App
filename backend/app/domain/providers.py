"""Model providers. Phase 11.

Almost every option worth using speaks the OpenAI chat-completions shape — Ollama,
Groq, OpenRouter, Together, Cerebras, LM Studio, vLLM, and Google's compatibility
endpoint. So there is one client here rather than one per vendor, and switching
provider is a base URL and a model name in `.env`.

It uses `httpx`, which FastAPI already depends on, so an open-model setup needs
**no additional package at all** — unlike the Anthropic SDK, which stays an
optional extra.

For financial data the local option is the one worth defaulting to. Merchant
names are a spending profile; Ollama keeps them on the machine, costs nothing,
and needs no key. The hosted options are here because they are faster and better
at the task, not because they are safer.
"""

from __future__ import annotations

import base64
import logging

import httpx

log = logging.getLogger("uvicorn.error")

#: Local models are slower than hosted ones and a receipt is not urgent.
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class ProviderError(Exception):
    """A call that did not come back usable. Never fatal to the caller."""


def chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    max_tokens: int,
    image: bytes | None = None,
    image_media_type: str = "image/jpeg",
) -> str:
    """One chat-completions call. Returns the reply text, or raises.

    Images go in the OpenAI `image_url` shape with a `data:` URI, which is what
    Ollama, OpenRouter and Groq all accept. Sending the bytes inline rather than
    a link matters here: a URL would mean hosting a photograph of a receipt
    somewhere, and the whole point of the local option is that it does not leave.
    """
    content: list[dict] | str
    if image is None:
        content = prompt
    else:
        encoded = base64.b64encode(image).decode()
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image_media_type};base64,{encoded}"},
            },
        ]

    headers = {"content-type": "application/json"}
    # Ollama and LM Studio need no key and reject a bogus Authorization header
    # on some builds, so it is omitted rather than faked.
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            timeout=TIMEOUT,
            json={
                "model": model,
                "max_tokens": max_tokens,
                # Some servers honour only one of these two names.
                "max_completion_tokens": max_tokens,
                "temperature": 0,
                "messages": [{"role": "user", "content": content}],
            },
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"could not reach {base_url}: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderError(
            f"{base_url} returned {response.status_code}: {response.text[:200]}"
        )

    try:
        body = response.json()
        return body["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"unexpected reply shape from {base_url}") from exc
