"""Small direct Foundry Chat Completions and native tool-call smoke."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def chat_url(base: str, deployment: str, explicit_url: str = "", api_version: str = "") -> str:
    if explicit_url:
        url = explicit_url
    else:
        base = base.rstrip("/") + "/"
        path = urllib.parse.urlsplit(base).path
        if not (path.endswith("/openai/v1/") or
                path.endswith(f"/managed-deployments/{deployment}/")):
            raise ValueError("Use the exact Foundry Consume base URL or GPT_OSS_CHAT_COMPLETIONS_URL")
        url = urllib.parse.urljoin(base, "chat/completions")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path.endswith("/chat/completions"):
        raise ValueError("Expected an HTTPS Chat Completions URL")
    if api_version:
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = [(k, v) for k, v in query if k != "api-version"]
        query.append(("api-version", api_version))
        url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    return url


def _request(url: str, key: str, payload: dict) -> tuple[dict, float]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Azure error bodies can echo configuration, so report only the status.
        raise RuntimeError(f"Chat Completions returned HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Chat Completions connection failed: {type(exc.reason).__name__}") from None
    return result, round(time.monotonic() - start, 3)


def run_smoke(*, deployment: str, url: str, key: str) -> dict:
    prompt = "Reply with exactly: gpt-oss Azure works"
    first, first_seconds = _request(url, key, {"model": deployment, "messages": [{"role": "user", "content": prompt}], "max_tokens": 128})
    text = first["choices"][0]["message"].get("content", "") or ""
    exact = text.strip() == "gpt-oss Azure works"
    effort_accepted = False
    try:
        _request(url, key, {"model": deployment, "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 128, "reasoning_effort": "medium"})
        effort_accepted = True
    except RuntimeError:
        pass
    tool = {"type": "function", "function": {"name": "get_magic_number", "description": "Return the magic number.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
    initial = [{"role": "user", "content": "Call get_magic_number, then tell me its result."}]
    call_result, tool_seconds = _request(url, key, {"model": deployment, "messages": initial,
                                                      "tools": [tool], "tool_choice": "auto", "max_tokens": 256})
    assistant_message = call_result["choices"][0]["message"]
    calls = assistant_message.get("tool_calls") or []
    matching = [c for c in calls if c.get("type") == "function" and
                c.get("function", {}).get("name") == "get_magic_number" and c.get("id")]
    result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "deployment": deployment,
              "route_type": "openai_v1" if "/openai/v1/" in url else "managed_deployments",
              "chat_completions_success": True, "exact_answer": exact,
              "direct_latency_seconds": first_seconds,
              "reasoning_effort_accepted": effort_accepted,
              "structured_tool_call": bool(matching), "tool_call_latency_seconds": tool_seconds,
              "tool_executed": False, "tool_output_returned": False, "final_answer": False}
    if not matching or len(matching) != len(calls):
        return result
    # Execute the only permitted local tool. Do not evaluate model arguments.
    tool_messages = [{"role": "tool", "tool_call_id": c["id"], "content": "42"} for c in calls]
    result["tool_executed"] = True
    final, final_seconds = _request(url, key, {"model": deployment,
        "messages": initial + [{"role": "assistant", "content": assistant_message.get("content"),
                                 "tool_calls": calls}] + tool_messages, "tools": [tool], "max_tokens": 256})
    result["tool_output_returned"] = True
    result["final_answer"] = "42" in (final["choices"][0]["message"].get("content") or "")
    result["final_latency_seconds"] = final_seconds
    return result


def main() -> None:
    load_dotenv(ROOT / ".env")
    deployment = os.getenv("GPT_OSS_DEPLOYMENT", "")
    key = os.getenv("GPT_OSS_API_KEY", "")
    base = os.getenv("GPT_OSS_API_BASE", "")
    if not all((deployment, key, base)):
        raise SystemExit("Configure GPT_OSS_DEPLOYMENT, GPT_OSS_API_BASE, and GPT_OSS_API_KEY in .env")
    url = chat_url(base, deployment, os.getenv("GPT_OSS_CHAT_COMPLETIONS_URL", ""),
                   os.getenv("GPT_OSS_API_VERSION", ""))
    result = run_smoke(deployment=deployment, url=url, key=key)
    out = ROOT / "audit" / "gpt_oss_20b" / "endpoint_smoke.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not all(result[k] for k in ("exact_answer", "structured_tool_call", "tool_executed",
                                   "tool_output_returned", "final_answer")):
        raise SystemExit("Native tool-call smoke failed; do not run SWE tasks")


if __name__ == "__main__":
    main()
