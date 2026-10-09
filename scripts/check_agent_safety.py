"""Read-only scan of generated/untrusted text (default: output/) for instruction-shaped
payloads (prompt-injection style). Flags only - never executes, deletes, or writes
anything, and makes no network call. See docs/REPOSITORY_AGENT_SAFETY.md.

    python scripts\\check_agent_safety.py                 scan output/ (or config.OUT_DIR)
    python scripts\\check_agent_safety.py --path DIR       scan a different directory
    python scripts\\check_agent_safety.py --max-bytes N    per-file read cap (default 2_000_000)

Exit codes: 0 = clean (including "nothing to scan"), 1 = findings printed, 2 = usage error.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import config  # noqa: E402
    DEFAULT_PATH = config.OUT_DIR
except Exception:
    DEFAULT_PATH = os.path.join(ROOT, "output")

DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_EXTS = ("json", "txt", "log")
SNIPPET_RADIUS = 20
SNIPPET_CAP = 120

# Each pattern requires co-occurrence of an imperative/directive shape with an
# agent-or-instruction-targeting term, never a bare keyword - a naive single-word sweep
# during this audit flagged "override" (cooldown override), the literal "Claude_WS" path
# substring, and "attribution" (a content-safety field name) as false positives.
PATTERNS = [
    ("IGNORE_INSTRUCTIONS", "HIGH",
     re.compile(r"\b(ignore|disregard|forget)\b.{0,20}\b(previous|prior|above|all)\b.{0,20}"
                r"\binstructions?\b", re.IGNORECASE | re.DOTALL)),
    ("NEW_INSTRUCTIONS_HEADER", "HIGH",
     re.compile(r"\bnew\s+instructions\b\s*[:\-]", re.IGNORECASE)),
    ("MODE_JAILBREAK", "HIGH",
     re.compile(r"\byou\s+are\s+now\b.{0,30}\b(developer|jailbreak|unrestricted|admin)\s+mode\b",
                re.IGNORECASE | re.DOTALL)),
    ("COMMIT_ATTRIBUTION_INJECTION", "HIGH",
     re.compile(r"\b(add|insert|include|append)\b.{0,40}\battribution\b.{0,40}\bcommits?\b",
                re.IGNORECASE | re.DOTALL)),
    ("OVERRIDE_TARGETING_AGENT", "MEDIUM",
     re.compile(r"\boverride\b.{0,40}\b(claude|assistant|agent|system\s+prompt)\b",
                re.IGNORECASE | re.DOTALL)),
    ("CLAUDE_TARGETED_IMPERATIVE", "MEDIUM",
     re.compile(r"\b(claude|assistant|agent)\b.{0,40}\b(must|should|will)\s+now\b",
                re.IGNORECASE | re.DOTALL)),
    ("HIDE_FROM_USER", "MEDIUM",
     re.compile(r"\bwithout\s+telling\s+the\s+user\b|\bdo\s+not\s+tell\s+the\s+(user|owner)\b",
                re.IGNORECASE)),
]

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


class Finding:
    __slots__ = ("path", "location", "severity", "name", "snippet")

    def __init__(self, path, location, severity, name, snippet):
        self.path = path
        self.location = location
        self.severity = severity
        self.name = name
        self.snippet = snippet


def _escape_snippet(text, start, end):
    lo = max(0, start - SNIPPET_RADIUS)
    hi = min(len(text), end + SNIPPET_RADIUS)
    snippet = text[lo:hi].replace("\n", "\\n").replace("\r", "\\r")
    if len(snippet) > SNIPPET_CAP:
        snippet = snippet[:SNIPPET_CAP] + "..."
    prefix = "..." if lo > 0 else ""
    suffix = "..." if hi < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def _scan_string(text, location):
    out = []
    for name, severity, pattern in PATTERNS:
        m = pattern.search(text)
        if m:
            out.append(Finding(None, location, severity, name,
                                _escape_snippet(text, m.start(), m.end())))
    return out


def extract_strings_from_json(obj, path=""):
    """Yield (location, string) for every string LEAF value - never a dict key, so a field
    name like "attribution" is out of scope by construction."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            for loc, s in extract_strings_from_json(value, child):
                yield loc, s
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            child = f"{path}[{idx}]"
            for loc, s in extract_strings_from_json(value, child):
                yield loc, s
    elif isinstance(obj, str):
        yield (path or "$"), obj


def read_capped(path, max_bytes):
    """Return (text, truncated) - UTF-8-safe, never raises on bad bytes."""
    with open(path, "rb") as fh:
        raw = fh.read(max_bytes)
    truncated = os.path.getsize(path) > max_bytes
    return raw.decode("utf-8", errors="replace"), truncated


def scan_json_text(text, path):
    findings = []
    try:
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return scan_text_lines(text, path)
    for location, s in extract_strings_from_json(data):
        for f in _scan_string(s, location):
            f.path = path
            findings.append(f)
    return findings


def scan_text_lines(text, path):
    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for f in _scan_string(line, f"line {lineno}"):
            f.path = path
            findings.append(f)
    return findings


def scan_file(path, max_bytes=DEFAULT_MAX_BYTES):
    text, _truncated = read_capped(path, max_bytes)
    if path.lower().endswith(".json"):
        return scan_json_text(text, path)
    return scan_text_lines(text, path)


def iter_candidate_files(root, exts):
    suffixes = tuple(f".{e.lower().lstrip('.')}" for e in exts)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            if name.lower().endswith(suffixes):
                yield full


def scan_tree(root, max_bytes=DEFAULT_MAX_BYTES, exts=DEFAULT_EXTS):
    if not os.path.isdir(root):
        return []
    findings = []
    for path in iter_candidate_files(root, exts):
        findings.extend(scan_file(path, max_bytes))
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.path, str(f.location)))
    return findings


def format_finding(f, root):
    try:
        rel = os.path.relpath(f.path, root)
    except ValueError:
        rel = f.path
    return f'{f.severity:<6} {rel}  {f.location}  "{f.snippet}"'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--path", default=DEFAULT_PATH, help="directory to scan")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help="per-file read cap")
    ap.add_argument("--ext", default=",".join(DEFAULT_EXTS),
                    help="comma-separated extensions to scan (default: json,txt,log)")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.path):
        print(f"Nothing to scan: {args.path} does not exist.")
        return 0

    exts = tuple(e.strip() for e in args.ext.split(",") if e.strip())
    findings = scan_tree(args.path, args.max_bytes, exts)
    for f in findings:
        print(format_finding(f, args.path))
    print(f"{len(findings)} finding(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
