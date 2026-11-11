"""Shared helpers for the board: config paths, time, TTL cache, redaction, HTTP.

Everything here is stdlib. Paths are module attributes read at call time, so
tests can point them at tmp files (``util.CFG["state_db"] = ...``).
"""
import datetime as _dt
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(APP_DIR)

CFG = {
    "tasklist_api": os.environ.get("ONEGUY_TASKLIST_API", "http://oneguy.invalid/t"),
    "state_db": os.path.join(HOME, ".hermes", "state.db"),
    "cron_jobs": os.path.join(HOME, ".hermes", "cron", "jobs.json"),
    "cron_output": os.path.join(HOME, ".hermes", "cron", "output"),
    "oneguy_dir": os.path.join(HOME, ".hermes", "1guy"),
    "media_blocks": os.path.join(REPO_ROOT, "media", "blocks.json"),
    "repo_root": REPO_ROOT,
    "projects_glob_root": os.path.join(HOME, "projects"),
    "logs_dir": os.path.join(HOME, ".hermes", "1guy", "logs"),
}


def oneguy(*parts):
    return os.path.join(CFG["oneguy_dir"], *parts)


# ---------- time ----------
def now():
    return time.time()


def iso(ts):
    """Epoch seconds -> local ISO-8601 with offset (or None)."""
    if ts is None:
        return None
    try:
        return _dt.datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_ts(val):
    """ISO string / epoch / None -> epoch seconds (or None). Naive ISO = local."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    try:
        return float(s)
    except ValueError:
        pass
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        if not m:
            return None
        d = _dt.datetime.fromisoformat(m.group(1))
    if d.tzinfo is None:
        d = d.astimezone()  # interpret as local time
    return d.timestamp()


def session_id_ts(sid):
    """Hermes session ids look like 20261103_091308_1cbd18 -> local epoch."""
    m = re.match(r"(\d{8})_(\d{6})_", sid or "")
    if not m:
        return None
    try:
        return _dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").astimezone().timestamp()
    except ValueError:
        return None


# ---------- text ----------
_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), "[redacted key]"),
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{12,}"), "[redacted]"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "[redacted]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[redacted]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "[redacted]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "[redacted jwt]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{12,}"), r"\1 [redacted]"),
    (re.compile(r"\b([A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASS|PASSWORD|PWD|CREDENTIALS?))"
                r"(\s*[:=]\s*)(['\"]?)[^\s'\",;]{6,}"), r"\1\2\3[redacted]"),
    # key=value / key: value where the key name says it's secret
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|secret|token|passw(?:or)?d|passwd|pwd|auth)[A-Z0-9_]*)"
                r"(\s*[:=]\s*)(['\"]?)[^\s'\",;]{6,}"), r"\1\2\3[redacted]"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[redacted hex]"),
    (re.compile(r"(?<![A-Za-z0-9+/_\-])[A-Za-z0-9+/_\-]{32,}={0,2}(?![A-Za-z0-9+/_\-])"), None),
]


def _blob(m):
    """Long base64/url-safe runs that look random (mixed case + digits, few
    path separators) are secrets; long paths and slugs are not."""
    s = m.group(0)
    seps = s.count("/") + s.count("-")
    if (re.search(r"\d", s) and re.search(r"[a-z]", s) and re.search(r"[A-Z]", s)
            and seps <= len(s) // 16):
        return "[redacted blob]"
    return s


def redact(text):
    if not text:
        return text
    out = str(text)
    for pat, rep in _SECRET_PATTERNS:
        out = pat.sub(rep if rep is not None else _blob, out)
    return out


def clip(text, n):
    """Collapse whitespace and cut to n chars with an ellipsis."""
    if text is None:
        return ""
    s = re.sub(r"\s+", " ", str(text)).strip()
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"


def safe_excerpt(text, n):
    """Redact first, then clip (so a clipped secret can't leak a prefix)."""
    return clip(redact(text), n)


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:48] or "x"


PALETTE = ["#5ad1c3", "#8fa6ff", "#e58fd0", "#9fd36b", "#f2867a", "#c7a3ff", "#6ec3f2", "#f5d76e"]


def color_for(slug, explicit=None):
    if explicit and re.fullmatch(r"#[0-9a-fA-F]{3,8}", explicit.strip()):
        return explicit.strip()
    if slug in ("1guy", "oneguy"):
        return "#ffb547"
    h = sum((i + 1) * ord(c) for i, c in enumerate(slug or ""))
    return PALETTE[h % len(PALETTE)]


# ---------- json files ----------
def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


# ---------- http ----------
class HttpError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def http_json(url, body=None, timeout=4.0):
    """GET (body None) or POST JSON; returns parsed JSON. Raises HttpError."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "1guy-board/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read()[:500].decode("utf-8", "replace")
        try:
            msg = json.loads(raw).get("detail") or raw
        except (ValueError, AttributeError):
            msg = raw
        raise HttpError(e.code, f"{e.code}: {msg}") from None
    except (urllib.error.URLError, OSError) as e:
        raise HttpError(502, f"unreachable: {getattr(e, 'reason', e)}") from None


# ---------- TTL cache (stale-while-revalidate, single flight) ----------
class SourceCache:
    """Per-source cache. Fresh value -> served. Stale value -> served at once
    and refreshed in a background thread (so warm requests never wait on a
    slow source). No value -> computed inline. Errors cache for ERR_TTL."""

    ERR_TTL = 5.0

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}   # name -> {"at": t, "ok": bool, "value"| "error"}
        self._inflight = set()

    def clear(self, name=None):
        with self._lock:
            if name is None:
                self._entries.clear()
            else:
                self._entries.pop(name, None)

    def _run(self, name, fn):
        t0 = time.time()
        try:
            entry = {"ok": True, "value": fn()}
        except Exception as e:  # a source failing must never break the board
            entry = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        entry["at"] = time.time()
        entry["ms"] = round((entry["at"] - t0) * 1000)
        with self._lock:
            prev = self._entries.get(name)
            # keep serving the last good value if a refresh fails, but say so
            if not entry["ok"] and prev and prev.get("ok"):
                prev = dict(prev)
                prev["stale_error"] = entry["error"]
                prev["at"] = entry["at"]  # retry again after another ttl
                self._entries[name] = prev
            else:
                self._entries[name] = entry
            self._inflight.discard(name)
        return self._entries[name]

    def get(self, name, ttl, fn, force=False):
        with self._lock:
            e = self._entries.get(name)
            age = (time.time() - e["at"]) if e else 0.0
            limit = ttl if (e and e.get("ok")) else self.ERR_TTL
            if e and not force and age < limit:
                return dict(e, age=round(age, 1), cached=True)
            if e and e.get("ok") and not force:
                if name not in self._inflight:
                    self._inflight.add(name)
                    threading.Thread(target=self._run, args=(name, fn), daemon=True).start()
                return dict(e, age=round(age, 1), cached=True, refreshing=True)
            self._inflight.add(name)
        return dict(self._run(name, fn), age=0.0, cached=False)


CACHE = SourceCache()
