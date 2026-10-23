"""A deliberately narrow, read-only MCP client for NSE's official MCP servers (research only).

docs/NSE_MCP_FEASIBILITY.md. Speaks just enough of MCP's streamable-HTTP transport
(JSON-RPC 2.0 over HTTPS POST, JSON or a single SSE `message` event back) to call
`initialize`, `tools/list` and `tools/call`. Nothing else: no resources, no prompts, no
sampling, no server-initiated requests are ever answered.

Safety contract (test-enforced in tests/test_nse_mcp_research.py):
- HTTPS only, and only the two endpoints NSE documents on https://www.nseindia.com/nse-mcp
  (`ALLOWED_ENDPOINTS`); a redirect is never followed.
- Bounded: per-request timeout, a hard per-client request budget, a response-size cap.
- No credentials: NSE documents the servers as "No Auth"; this client sends no cookie,
  token or API key, and never reads one from the environment.
- Untrusted data: a response is parsed as JSON and shape-checked, nothing more. Text inside
  a tool result is returned as data and never interpreted as an instruction.
- Read-only by construction: `call_tool` refuses any tool the server did not list AND any
  tool outside `RESEARCH_TOOLS`, an explicit allowlist of documented read-only queries.

Never imported by the production pipeline (test-enforced); no adapter is wired anywhere.
"""
from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field

import requests

BHAVCOPY_CM = "https://mcp.nseindia.in/bhavcopy/cm/mcp"
CM_MARKET = "https://mcp.nseindia.in/cmmkt/mcp"
ALLOWED_ENDPOINTS = frozenset({BHAVCOPY_CM, CM_MARKET})

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "dmb-nse-mcp-research", "version": "0"}
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_REQUESTS = 40
MAX_RESPONSE_BYTES = 5_000_000
# The ONLY tools this client will call: every one is a documented read-only query (verified
# 2026-10-23 from the servers' own tools/list - NSE sets no readOnlyHint annotation, so the
# allowlist is explicit rather than inferred from a name).
RESEARCH_TOOLS = frozenset({
    "get_stock_history", "get_bulk_quote", "get_corporate_actions", "get_market_breadth",
    "get_index_snapshot", "get_index_history", "get_top_movers", "nse_lookup_symbol",
    "cm_get_data_status", "cm_get_allstocks_status", "cm_get_stock_quote",
})
SESSION_404_RETRIES = 2


class McpResearchError(RuntimeError):
    """Any refusal or protocol failure. The message never contains a response body verbatim
    beyond a short, length-capped excerpt."""


class _Session404(Exception):
    """Internal: a session-bound request hit a node that does not hold the session."""


def _excerpt(text: str, n: int = 200) -> str:
    return (text or "")[:n].replace("\n", " ")


def parse_body(content_type: str, raw: bytes) -> dict:
    """One JSON-RPC response from either transport shape. SSE: the LAST `data:` payload of a
    `message` event (servers may send only one). Anything else is a protocol error."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise McpResearchError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
    text = raw.decode("utf-8", errors="replace")
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype == "text/event-stream":
        payloads, buf = [], []
        for line in text.splitlines():
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
            elif not line.strip() and buf:
                payloads.append("\n".join(buf))
                buf = []
        if buf:
            payloads.append("\n".join(buf))
        if not payloads:
            raise McpResearchError("SSE response carried no data event")
        text = payloads[-1]
    elif ctype != "application/json":
        raise McpResearchError(f"unexpected content-type {ctype!r}: {_excerpt(text)}")
    try:
        msg = json.loads(text)
    except ValueError as exc:
        raise McpResearchError(f"response is not JSON: {_excerpt(text)}") from exc
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        raise McpResearchError("response is not a JSON-RPC 2.0 object")
    return msg


def _read_body(resp) -> bytes:
    """The response body, capped at MAX_RESPONSE_BYTES + 1. An SSE stream may stay open after
    its one `message` event, so stop at the first blank line that ends a `data:` event rather
    than waiting for the server to close it (which would only end at the read timeout)."""
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "text/event-stream":
        return resp.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
    out, seen_data = bytearray(), False
    for line in resp.iter_lines(chunk_size=8192, delimiter=None):
        out += line + b"\n"
        if len(out) > MAX_RESPONSE_BYTES:
            break
        if line.startswith(b"data:"):
            seen_data = True
        elif not line.strip() and seen_data:
            break
    resp.close()
    return bytes(out)


@dataclass
class CallRecord:
    method: str
    tool: str | None
    status: int | None
    elapsed_s: float
    bytes: int
    ok: bool
    error: str | None = None


@dataclass
class McpResearchClient:
    endpoint: str
    timeout: float = DEFAULT_TIMEOUT
    max_requests: int = DEFAULT_MAX_REQUESTS
    session: requests.Session = field(default_factory=requests.Session)
    calls: list = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _session_id: str | None = None
    _tools: dict | None = None
    server_info: dict | None = None

    def __post_init__(self):
        if self.endpoint not in ALLOWED_ENDPOINTS:
            raise McpResearchError(f"endpoint not on the allowlist: {self.endpoint!r}")
        # Never inherit proxy credentials / netrc auth from the environment.
        self.session.trust_env = False

    # -- transport -------------------------------------------------------------------
    def _post(self, payload: dict, *, tool: str | None = None) -> dict | None:
        """Verified 2026-10-23: NSE's servers return an `Mcp-Session-Id` but no affinity
        cookie, and the backend pool is not sticky - a session-bound request intermittently
        lands on a node that never saw the session and gets HTTP 404. Retry exactly that case,
        at most `SESSION_404_RETRIES` times, each retry counted against the request budget."""
        for attempt in range(SESSION_404_RETRIES + 1):
            try:
                return self._post_once(payload, tool=tool)
            except _Session404:
                if attempt == SESSION_404_RETRIES:
                    raise McpResearchError(
                        f"HTTP 404 (session not found) after {attempt + 1} attempts") from None

    def _post_once(self, payload: dict, *, tool: str | None = None) -> dict | None:
        if len(self.calls) >= self.max_requests:
            raise McpResearchError(f"request budget exhausted ({self.max_requests})")
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL_VERSION}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        t0 = time.monotonic()
        status, size = None, 0
        try:
            resp = self.session.post(self.endpoint, json=payload, headers=headers,
                                     timeout=self.timeout, allow_redirects=False, stream=True)
            status = resp.status_code
            raw = _read_body(resp)
            size = len(raw)
            if 300 <= status < 400:
                raise McpResearchError(f"redirect refused (HTTP {status})")
            if status == 404 and self._session_id:
                self.calls.append(CallRecord(payload["method"], tool, status,
                                             time.monotonic() - t0, size, False,
                                             "HTTP 404 (session not found on this node)"))
                raise _Session404()
            sid = resp.headers.get("Mcp-Session-Id")
            if sid and len(sid) < 256:
                self._session_id = sid
            if "id" not in payload:          # a notification: 202 Accepted, no body
                if status not in (200, 202, 204):
                    raise McpResearchError(f"notification refused (HTTP {status})")
                self.calls.append(CallRecord(payload["method"], tool, status,
                                             time.monotonic() - t0, size, True))
                return None
            if status != 200:
                raise McpResearchError(f"HTTP {status}: {_excerpt(raw.decode('utf-8', 'replace'))}")
            msg = parse_body(resp.headers.get("Content-Type", ""), raw)
            if msg.get("id") != payload["id"]:
                raise McpResearchError("JSON-RPC id mismatch")
            if "error" in msg:
                err = msg["error"] if isinstance(msg["error"], dict) else {}
                raise McpResearchError(f"JSON-RPC error {err.get('code')}: "
                                       f"{_excerpt(str(err.get('message')))}")
            if not isinstance(msg.get("result"), dict):
                raise McpResearchError("JSON-RPC result is not an object")
            self.calls.append(CallRecord(payload["method"], tool, status,
                                         time.monotonic() - t0, size, True))
            return msg["result"]
        except McpResearchError as exc:
            self.calls.append(CallRecord(payload["method"], tool, status,
                                         time.monotonic() - t0, size, False, str(exc)))
            raise
        except requests.RequestException as exc:
            self.calls.append(CallRecord(payload["method"], tool, status,
                                         time.monotonic() - t0, size, False, type(exc).__name__))
            raise McpResearchError(f"transport error: {type(exc).__name__}") from exc

    def _request(self, method: str, params: dict | None = None, *, tool: str | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            payload["params"] = params
        return self._post(payload, tool=tool)

    # -- MCP ---------------------------------------------------------------------------
    def initialize(self) -> dict:
        result = self._request("initialize", {"protocolVersion": PROTOCOL_VERSION,
                                              "capabilities": {}, "clientInfo": CLIENT_INFO})
        self.server_info = {"protocolVersion": result.get("protocolVersion"),
                            "serverInfo": result.get("serverInfo"),
                            "capabilities": sorted((result.get("capabilities") or {}).keys())}
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.server_info

    def list_tools(self) -> list:
        tools, cursor = [], None
        for _ in range(10):                              # bounded pagination
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            page = result.get("tools")
            if not isinstance(page, list):
                raise McpResearchError("tools/list result has no tools array")
            for t in page:
                if isinstance(t, dict) and isinstance(t.get("name"), str):
                    tools.append({"name": t["name"],
                                  "description": str(t.get("description") or ""),
                                  "inputSchema": t.get("inputSchema") or {},
                                  "annotations": t.get("annotations") or {}})
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self._tools = {t["name"]: t for t in tools}
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        if self._tools is None:
            raise McpResearchError("call list_tools() first - only listed tools may be called")
        if name not in self._tools:
            raise McpResearchError(f"tool not listed by the server: {name!r}")
        if name not in RESEARCH_TOOLS:
            raise McpResearchError(f"tool is not on the research allowlist: {name!r}")
        result = self._request("tools/call", {"name": name, "arguments": arguments}, tool=name)
        return tool_payload(result)


def tool_payload(result: dict) -> dict:
    """`{"is_error", "structured", "text"}` from a tools/call result. `structured` is the
    `structuredContent` object when present, else the first text block parsed as JSON when it
    parses; `text` is the concatenated text blocks, kept only as data."""
    texts = [c.get("text", "") for c in result.get("content") or []
             if isinstance(c, dict) and c.get("type") == "text"]
    structured = result.get("structuredContent")
    if structured is None and texts:
        try:
            structured = json.loads(texts[0])
        except ValueError:
            structured = None
    return {"is_error": bool(result.get("isError")), "structured": structured,
            "text": "\n".join(texts)}
