"""One real Gemini request through the hook engine's exact contract - a diagnostic, never a path
that feeds a video.

    python -m operations.gemini_smoke [--out output/pre_shadow_readiness/gemini_smoke_test.json]

It answers: does the configured key authenticate, does billing/quota admit the request, does the
configured model answer, and does the answer parse and validate against the hook contract
(`hooks.gemini.build_prompt` / `response_schema` / `parse`, `hooks.validation.validate_response`)?

Deliberately NOT `news.ask_gemini`: that client retries and swallows the HTTP status into a
graceful `None` (right for production, useless for a smoke test). This sends the SAME request
body `hooks.gemini.default_client` sends - same endpoint, model, `x-goog-api-key` header,
generation config, no search tool - exactly ONCE, and reports the raw status, the rate-limit /
quota headers and the error body. No fallback hides a 429/503 here.

The input is a SYNTHETIC hook fixture (`hooks.fixtures`, placeholder names STOCK-A...): the test
is about the API and the contract, not market data. The key is sent only in the request header;
it is never printed, written or echoed - every string that leaves this module passes `_redact`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

SMOKE_VERSION = "gemini-smoke-1.0"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Response headers worth reporting (quota / rate limiting / request tracing). Server headers never
# carry the caller's key, but they go through `_redact` anyway.
_HEADER_HINTS = ("ratelimit", "rate-limit", "quota", "retry-after", "x-goog", "server-timing",
                 "x-request-id", "date")


def _redact(text, key: str | None) -> str:
    text = "" if text is None else str(text)
    if key:
        text = text.replace(key, "***REDACTED***")
    return text


def _interesting_headers(headers, key) -> dict:
    out = {}
    for name, value in dict(headers or {}).items():
        if any(h in name.lower() for h in _HEADER_HINTS):
            out[name] = _redact(value, key)
    return out


def hook_request(fixture: str = "post_quiet") -> tuple:
    """(sheet, candidates, prompt, schema, generation_config) for a synthetic POST hook sheet -
    built by the production hook code, unchanged."""
    from hooks import build_candidates, fixtures, post_market_sheet
    from hooks.gemini import build_prompt, response_schema
    b = getattr(fixtures, fixture)()
    sheet = post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"], b["sections"],
                              b["universe"])
    cands = build_candidates(sheet)
    schema = response_schema(sheet, cands)
    # identical to hooks.gemini.default_client's generation config
    gen = {"temperature": 0.5, "responseMimeType": "application/json", "responseSchema": schema}
    return sheet, cands, build_prompt(sheet, cands), schema, gen


def run_smoke_test(*, fixture: str = "post_quiet", timeout: float = 60.0, post=None,
                   now: dt.datetime | None = None) -> dict:
    """ONE request, no retry. `post` is the HTTP seam (defaults to `requests.post`)."""
    import config
    from hooks.gemini import parse
    from hooks.validation import validate_response

    key = os.getenv("GEMINI_API_KEY") or None
    model = config.GEMINI_MODEL
    result = {"version": SMOKE_VERSION, "run_at": (now or dt.datetime.now(dt.timezone.utc)).isoformat(),
              "model": model, "endpoint": ENDPOINT.format(model=model), "fixture": fixture,
              "fixture_note": "synthetic placeholder sheet (STOCK-A...) - no market data",
              "key_configured": bool(key), "key_location": "request header x-goog-api-key only",
              "retries": 0, "fallback_used_by_smoke_test": False}
    if not key:
        result.update(request_result="NO_KEY", http_status=None, api_working=False,
                      structured_json_parsed=False, contract_validation_passed=False,
                      production_fallback_needed=True,
                      next_diagnostic="set GEMINI_API_KEY (env or git-ignored .env) and rerun")
        return result

    sheet, cands, prompt, schema, gen = hook_request(fixture)
    result["candidates_offered"] = [c.candidate_id for c in cands]
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}
    if post is None:
        import requests
        post = requests.post
    t0 = time.time()
    try:
        r = post(result["endpoint"], json=body, timeout=timeout,
                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    except Exception as exc:
        name = type(exc).__name__
        result.update(request_result="TIMEOUT" if "timeout" in name.lower() else "NETWORK_ERROR",
                      http_status=None, error=_redact(f"{name}: {exc}", key)[:500],
                      elapsed_ms=round((time.time() - t0) * 1000), api_working=False,
                      structured_json_parsed=False, contract_validation_passed=False,
                      production_fallback_needed=True,
                      next_diagnostic="rerun once; if it repeats, check outbound HTTPS to "
                                      "generativelanguage.googleapis.com from this machine")
        return result

    result["elapsed_ms"] = round((time.time() - t0) * 1000)
    result["http_status"] = r.status_code
    result["response_headers"] = _interesting_headers(getattr(r, "headers", {}), key)
    text = _redact(getattr(r, "text", ""), key)
    try:
        payload = r.json()
    except Exception:
        payload = None

    if r.status_code != 200:
        err = (payload or {}).get("error") if isinstance(payload, dict) else None
        err = err if isinstance(err, dict) else {}
        details = err.get("details") or []
        result.update(
            request_result={429: "QUOTA_OR_RATE_LIMITED", 403: "PERMISSION_DENIED",
                            401: "UNAUTHENTICATED", 404: "NOT_FOUND", 503: "UNAVAILABLE",
                            500: "SERVER_ERROR"}.get(r.status_code, "HTTP_ERROR"),
            api_status=err.get("status"), error_message=_redact(err.get("message"), key)[:600],
            error_details=json.loads(_redact(json.dumps(details, default=str), key))[:6],
            raw_body_excerpt=None if err else text[:600],
            api_working=False, structured_json_parsed=False, contract_validation_passed=False,
            production_fallback_needed=True,
            next_diagnostic={
                429: "open Google AI Studio / Cloud console > Gemini API > Quotas for this key's "
                     "project and confirm the billing account is linked to THAT project and the "
                     "model's paid-tier RPM/RPD limits are shown (a key from a different, "
                     "free-tier project keeps returning 429)",
                503: "rerun once in a few minutes; a repeated 503 on the same model is the known "
                     "server-side issue - compare with one request to another GA model",
                404: "list models for this key (GET v1beta/models) and confirm GEMINI_MODEL is "
                     "available to it; intermittent 404s are the known server-side issue",
                403: "the key's project lacks the Generative Language API or the key is "
                     "restricted - check API restrictions on the key",
                401: "the key is invalid or revoked - regenerate it in AI Studio",
            }.get(r.status_code, "inspect error_message above"))
        return result

    usage = (payload or {}).get("usageMetadata") if isinstance(payload, dict) else None
    result["model_version"] = (payload or {}).get("modelVersion") if isinstance(payload, dict) else None
    result["usage_metadata"] = usage
    try:
        cand0 = payload["candidates"][0]
        raw = "".join(p.get("text", "") for p in cand0["content"]["parts"])
        result["finish_reason"] = cand0.get("finishReason")
    except Exception as exc:
        result.update(request_result="MALFORMED_RESPONSE", api_working=True,
                      structured_json_parsed=False, contract_validation_passed=False,
                      production_fallback_needed=True,
                      error=f"no candidates/parts in a 200 response ({type(exc).__name__})",
                      raw_body_excerpt=text[:600])
        return result

    obj = parse(raw)
    result["request_result"] = "OK"
    result["api_working"] = True
    result["structured_json_parsed"] = obj is not None
    result["response_excerpt"] = _redact(raw, key)[:1200]
    if obj is None:
        result.update(contract_validation_passed=False, production_fallback_needed=True,
                      production_outcome="DETERMINISTIC (reply was not a JSON object)")
        return result
    s_issues, t_issues, cand, cur, summ = validate_response(obj, sheet, cands)
    result["structure_issues"] = s_issues
    result["text_issues"] = t_issues
    result["chosen"] = {"candidate_id": obj.get("candidate_id"), "hero_visual": obj.get("hero_visual"),
                        "teaser_beats": obj.get("teaser_beats"), "curiosity_line": cur,
                        "summary_line": summ}
    result["contract_validation_passed"] = not s_issues and not t_issues
    # what plan_hook would have done with this exact reply (hooks.engine, read-only mirror)
    result["production_outcome"] = ("GEMINI" if not s_issues and not t_issues else
                                    "GEMINI_CHOICE_TEMPLATE_TEXT or DETERMINISTIC (lines failed "
                                    "text validation)" if not s_issues else
                                    "DETERMINISTIC (choice failed structural validation)")
    result["production_fallback_needed"] = bool(s_issues or t_issues)
    return result


def write_result(result: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False, default=str)
    return path


def main(argv=None) -> int:
    import argparse
    import config
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(config.OUT_DIR, "pre_shadow_readiness",
                                                  "gemini_smoke_test.json"))
    ap.add_argument("--fixture", default="post_quiet")
    args = ap.parse_args(argv)
    result = run_smoke_test(fixture=args.fixture)
    path = write_result(result, args.out)
    print(f"gemini smoke test: {result['request_result']} (HTTP {result.get('http_status')}) "
          f"model={result['model']} parsed={result.get('structured_json_parsed')} "
          f"contract={result.get('contract_validation_passed')} -> {path}")
    return 0 if result.get("request_result") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_smoke_test", "hook_request", "write_result", "SMOKE_VERSION"]
