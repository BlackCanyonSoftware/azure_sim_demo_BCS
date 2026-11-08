"""The assistant: one call to a chat model with the instructions in prompt.md, asking for JSON back.

Which model:
  AI_ENDPOINT + AI_DEPLOYMENT   Azure OpenAI, signed in with the app's identity (no key). What the deployed app uses.
  AI_BASE_URL + AI_MODEL        any OpenAI-compatible server, e.g. KPrime's local models (http://127.0.0.1:8081/v1) while building it.
"""

import json
import os
from pathlib import Path

INSTRUCTIONS = Path(__file__).resolve().parent / "prompt.md"
_client: dict = {}


class NotConfigured(Exception):
    pass


class ModelError(Exception):
    pass


def _model():
    if "c" in _client:
        return _client["c"]
    from openai import AzureOpenAI, OpenAI  # noqa: PLC0415

    if os.environ.get("AI_ENDPOINT"):
        from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider  # noqa: PLC0415

        cred = ManagedIdentityCredential() if os.environ.get("IDENTITY_ENDPOINT") else DefaultAzureCredential(exclude_interactive_browser_credential=True)
        client = AzureOpenAI(azure_endpoint=os.environ["AI_ENDPOINT"], api_version=os.environ.get("AI_API_VERSION", "2024-10-21"),
                             azure_ad_token_provider=get_bearer_token_provider(cred, "https://cognitiveservices.azure.com/.default"))
        model = os.environ.get("AI_DEPLOYMENT", "gpt-4.1-mini")
    elif os.environ.get("AI_BASE_URL"):
        client = OpenAI(base_url=os.environ["AI_BASE_URL"], api_key=os.environ.get("AI_API_KEY", "local"))
        model = os.environ.get("AI_MODEL", "")
        if not model:
            raise NotConfigured("AI_MODEL is not set (the model name the local server knows)")
    else:
        raise NotConfigured("no model is set up: AI_ENDPOINT (Azure OpenAI) or AI_BASE_URL (a local server)")
    _client["c"] = (client, model)
    return _client["c"]


def answer(prompt: str, messages=None) -> dict:
    client, model = _model()
    limit = int(os.environ.get("AI_MAX_TOKENS", "900"))
    # 60 s suits Azure; KPrime's local model wants longer (run.sh sets it) — a full structured brief can take minutes there
    timeout = int(os.environ.get("AI_TIMEOUT_SECONDS", "60"))
    # Azure's newer models (gpt-5-*) take only max_completion_tokens; local servers know max_tokens
    size = {"max_completion_tokens": limit} if os.environ.get("AI_ENDPOINT") else {"max_tokens": limit}
    msgs = messages or [{"role": "system", "content": INSTRUCTIONS.read_text()}, {"role": "user", "content": prompt}]
    try:
        reply = client.chat.completions.create(
            model=model,
            messages=msgs,
            response_format={"type": "json_object"},
            timeout=timeout,
            **size,
        )
    except Exception as exc:  # noqa: BLE001 — network, throttling (429), content filter: all reported the same way
        raise ModelError(f"{exc.__class__.__name__}: {str(exc)[:300]}") from exc
    text = reply.choices[0].message.content or "{}"
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}
