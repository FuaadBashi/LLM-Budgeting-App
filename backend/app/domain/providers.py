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

#: Optional model decoration must fail boundedly. Receipt reading is the only
#: required call and 30 seconds is still a generous local-model budget.
TIMEOUT = httpx.Timeout(30.0, connect=5.0, write=10.0, pool=5.0)


class ProviderError(Exception):
    """A call that did not come back usable. Never fatal to the caller."""


#: Endpoints that answered a malformed-request status to a `response_format`
#: field. `response_format` is an OpenAI extension and this app deliberately
#: targets whatever speaks the shape -- Ollama, LM Studio, vLLM, Groq,
#: OpenRouter, Together -- across versions we do not control. A server that
#: rejects it gets one retry without it, and is remembered, because a local
#: model's round trip is seconds and paying a doomed one on every later call
#: would be worse than never asking. Only the "your request is malformed"
#: statuses are remembered: a 503 from a model that had not finished loading
#: must not disable JSON mode for the life of the process.
_no_json_mode: set[str] = set()
_MALFORMED = frozenset({400, 404, 415, 422})


def chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    max_tokens: int,
    image: bytes | None = None,
    image_media_type: str = "image/jpeg",
    json_object: bool = False,
) -> str:
    """One chat-completions call. Returns the reply text, or raises.

    Images go in the OpenAI `image_url` shape with a `data:` URI, which is what
    Ollama, OpenRouter and Groq all accept. Sending the bytes inline rather than
    a link matters here: a URL would mean hosting a photograph of a receipt
    somewhere, and the whole point of the local option is that it does not leave.

    `json_object` asks the server to constrain the reply to a JSON object. It is
    per call site rather than always-on because some callers want prose, and it
    is a hint rather than a contract: a server that refuses the field is retried
    without it, so the reply still arrives and the caller's own parser still has
    to be tolerant. It narrows the failure mode; it does not remove it.
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

    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        # Some servers honour only one of these two names.
        "max_completion_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": content}],
    }
    asked_for_json = json_object and endpoint not in _no_json_mode
    if asked_for_json:
        payload["response_format"] = {"type": "json_object"}

    response = _post(endpoint, headers, payload)

    if response.status_code >= 400 and asked_for_json:
        # The field is the only thing that separates this attempt from a call
        # shape known to work, so drop it and try once more. If the failure was
        # something else the second attempt fails the same way and that error
        # is the one raised -- the retry cannot hide a real problem.
        if response.status_code in _MALFORMED:
            _no_json_mode.add(endpoint)
        payload.pop("response_format")
        response = _post(endpoint, headers, payload)

    if response.status_code >= 400:
        raise ProviderError(
            f"{base_url} returned {response.status_code}: {response.text[:200]}"
        )

    try:
        body = response.json()
        return body["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"unexpected reply shape from {base_url}") from exc


def _post(endpoint: str, headers: dict[str, str], payload: dict) -> httpx.Response:
    try:
        return httpx.post(endpoint, headers=headers, timeout=TIMEOUT, json=payload)
    except httpx.HTTPError as exc:
        # A transport failure is not a rejected field: it must not be retried
        # here, because a server that is down would then cost two full timeouts.
        raise ProviderError(f"could not reach {endpoint}: {exc}") from exc
