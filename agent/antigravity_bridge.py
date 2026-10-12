#!/usr/bin/env python3
"""Google Antigravity as a Nexus brain.

Drives the Antigravity desktop app's own ``agentapi`` CLI (shipped inside the
app as ``language_server agentapi``) and streams the agent's answer back by
tailing the conversation transcript Antigravity writes to disk.

    agentapi new-conversation [--model=flash_lite|flash|pro] [--title=T] <prompt>
        -> {"response": {"newConversation": {"conversationId": "..."}}}
    agentapi send-message [--title=T] <conversation_id> <content>

Transcript: ~/.gemini/antigravity/brain/<conversationId>/.system_generated/logs/transcript.jsonl
(one JSON step per line: step_index, type, status, content, tool_calls, ...).

Environment the CLI needs (discovered at call time, never inherited):
    ANTIGRAVITY_LS_ADDRESS   localhost:<port> of the running language_server
    ANTIGRAVITY_CSRF_TOKEN   from the language_server's --csrf_token argument
    ANTIGRAVITY_PROJECT_ID   the Nexus project registered under ~/.gemini/config/projects/

CLI:
    antigravity_bridge.py status
    antigravity_bridge.py chat <session_id> <message> [--model flash]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"
APP_LS = Path("/Applications/Antigravity.app/Contents/Resources/bin/language_server")   # macOS
MODELS = ("flash_lite", "flash", "pro")
DEFAULT_TIMEOUT = float(os.environ.get("NEXUS_ANTIGRAVITY_TIMEOUT", "300"))
SETTLE_S = 2.0          # quiet time after a final answer before we call it complete
POLL_S = 0.4
# Steps Antigravity writes around a turn that never mean "the agent is still working".
PASSIVE_STEPS = frozenset({"CHECKPOINT", "CONVERSATION_HISTORY", "USER_INPUT", "EPHEMERAL_MESSAGE"})


class AntigravityError(RuntimeError):
    """A user-facing problem: Antigravity not installed / not running / not logged in."""


# =============================================================================
# Paths (overridable for tests)
# =============================================================================
def gemini_home() -> Path:
    return Path(os.environ.get("NEXUS_GEMINI_HOME") or (Path.home() / ".gemini"))


def projects_dir() -> Path:
    return gemini_home() / "config" / "projects"


def brain_root() -> Path:
    return gemini_home() / "antigravity" / "brain"


def transcript_path(conversation_id: str) -> Path:
    return brain_root() / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"


def state_dir() -> Path:
    return Path(os.environ.get("NEXUS_ANTIGRAVITY_DIR") or (ROOT / "data" / "antigravity"))


def nexus_folder() -> Path:
    return Path(os.environ.get("NEXUS_ANTIGRAVITY_FOLDER") or ROOT).resolve()


def app_language_servers() -> List[Path]:
    """Candidate language_server binaries shipped inside the Antigravity app.

    macOS is tested. Windows/Linux locations are best guesses (UNTESTED): the
    Windows installer is assumed to install per-user under
    %LOCALAPPDATA%\\Programs\\Antigravity like other Electron/VS Code forks.
    NEXUS_ANTIGRAVITY_LS overrides everything."""
    env = os.environ.get("NEXUS_ANTIGRAVITY_LS")
    if env:
        return [Path(env)]
    if IS_WINDOWS:
        roots = [Path(os.environ[k]) / "Programs" / "Antigravity" for k in ("LOCALAPPDATA",) if os.environ.get(k)]
        roots += [Path(os.environ[k]) / "Antigravity" for k in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(k)]
        out = []
        for r in roots:
            for sub in ("resources/bin", "resources/app/bin", "bin"):
                out += [r / sub / "language_server.exe"] + sorted((r / sub).glob("language_server*.exe")) \
                    if (r / sub).is_dir() else [r / sub / "language_server.exe"]
        return out
    if sys.platform.startswith("linux"):                       # UNTESTED
        return [Path("/usr/share/antigravity/resources/bin/language_server"),
                Path("/opt/Antigravity/resources/bin/language_server")]
    return [APP_LS]


def agentapi_wrappers() -> List[Path]:
    base = gemini_home() / "antigravity" / "bin"
    names = ("agentapi.exe", "agentapi.cmd", "agentapi.bat", "agentapi") if IS_WINDOWS else ("agentapi",)
    return [base / n for n in names]


def cli_path() -> List[str]:
    """argv prefix for agentapi. The ~/.gemini wrapper just execs the app binary."""
    for ls in app_language_servers():
        if ls.exists():
            return [str(ls), "agentapi"]
    for wrapper in agentapi_wrappers():
        if wrapper.exists():
            if wrapper.suffix.lower() in (".cmd", ".bat"):          # Windows batch wrapper (UNTESTED)
                return [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(wrapper)]
            return [str(wrapper)]
    where = "%LOCALAPPDATA%\\Programs\\Antigravity" if IS_WINDOWS else "/Applications/Antigravity.app"
    raise AntigravityError(f"Google Antigravity is not installed (no {where} "
                           "and no ~/.gemini/antigravity/bin/agentapi). Set NEXUS_ANTIGRAVITY_LS to the "
                           "app's language_server binary if it lives elsewhere.")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# =============================================================================
# Discovery: running language_server -> csrf token + listening ports
# =============================================================================
def _run(args: List[str], timeout: float = 10) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _process_list() -> str:
    """'<pid> <command line>' per line, for every process (best effort)."""
    try:                                   # optional; not a dependency
        import psutil  # type: ignore
        rows = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if cmd:
                rows.append(f"{p.info['pid']} {cmd}")
        if rows:
            return "\n".join(rows)
    except Exception:                      # noqa: BLE001  ImportError, AccessDenied, ...
        pass
    if IS_WINDOWS:                         # UNTESTED on real Windows
        ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'language_server%'\" | "
              "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }")
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
        if out.strip():
            return out
        return parse_wmic(_run(["wmic", "process", "where", "name like 'language_server%'",
                                "get", "ProcessId,CommandLine", "/format:csv"]))
    return _run(["/bin/ps", "-axww", "-o", "pid=,command="])


def parse_wmic(csv_out: str) -> str:
    """wmic /format:csv (Node,CommandLine,ProcessId) → '<pid> <command>' lines."""
    rows = []
    for line in csv_out.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 3 and parts[-1].strip().isdigit():
            rows.append(f"{parts[-1].strip()} {','.join(parts[1:-1])}")
    return "\n".join(rows)


def find_language_servers(ps_output: Optional[str] = None) -> List[Tuple[int, str]]:
    """[(pid, csrf_token)] for every running Antigravity language_server."""
    out = ps_output if ps_output is not None else _process_list()
    found = []
    for line in out.splitlines():
        line = line.strip()
        if "language_server" not in line or " agentapi" in line:
            continue
        m = re.match(r"(\d+)\s+(.*)$", line)
        tok = re.search(r"--csrf_token[= ](\S+)", line)
        if m and tok:
            found.append((int(m.group(1)), tok.group(1)))
    return found


def netstat_ports(pid: int, netstat_output: str) -> List[int]:
    """Windows `netstat -ano -p TCP`: 'TCP  127.0.0.1:53210  0.0.0.0:0  LISTENING  1234'."""
    ports = []
    for line in netstat_output.splitlines():
        cols = line.split()
        if len(cols) >= 5 and cols[0].upper() == "TCP" and cols[3].upper() == "LISTENING" and cols[4] == str(pid):
            host, _, port = cols[1].rpartition(":")
            if port.isdigit() and host in ("127.0.0.1", "0.0.0.0", "[::1]", "[::]") and int(port) not in ports:
                ports.append(int(port))
    return ports


def listening_ports(pid: int, lsof_output: Optional[str] = None) -> List[int]:
    if lsof_output is None and IS_WINDOWS:                      # UNTESTED on real Windows
        return netstat_ports(pid, _run(["netstat", "-ano", "-p", "TCP"]))
    out = lsof_output if lsof_output is not None else _run(
        ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"])
    ports = []
    for m in re.finditer(r"(?:127\.0\.0\.1|localhost|\[::1\]|\*):(\d+)\s+\(LISTEN\)", out):
        p = int(m.group(1))
        if p not in ports:
            ports.append(p)
    return ports


_cache_lock = threading.Lock()
_good_address: Dict[str, str] = {}      # csrf token -> address that worked


def discover() -> dict:
    """{"csrf": str, "addresses": [localhost:port, ...]} or AntigravityError."""
    cli_path()
    servers = find_language_servers()
    if not servers:
        raise AntigravityError("Google Antigravity is not running. Open the Antigravity app "
                               "(and sign in), then try again.")
    pid, csrf = servers[0]
    ports = listening_ports(pid)
    if not ports:
        raise AntigravityError(f"Antigravity language_server (pid {pid}) has no listening port yet; "
                               "wait for the app to finish starting.")
    addrs = [f"localhost:{p}" for p in ports]
    with _cache_lock:
        good = _good_address.get(csrf)
    if good in addrs:
        addrs.remove(good)
        addrs.insert(0, good)
    return {"pid": pid, "csrf": csrf, "addresses": addrs}


def sanitized_env(address: str, csrf: str, project_id: Optional[str]) -> Dict[str, str]:
    keep = ("PATH", "HOME", "USER", "LANG")
    if IS_WINDOWS:   # Windows processes need these to start at all (UNTESTED)
        keep += ("SYSTEMROOT", "SystemRoot", "WINDIR", "USERPROFILE", "USERNAME", "LOCALAPPDATA", "APPDATA",
                 "TEMP", "TMP", "PATHEXT", "COMSPEC", "ProgramFiles", "ProgramData")
    env = {k: os.environ[k] for k in keep if os.environ.get(k)}
    if not IS_WINDOWS:
        env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    env.setdefault("HOME", str(Path.home()))
    env["ANTIGRAVITY_LS_ADDRESS"] = address
    env["ANTIGRAVITY_CSRF_TOKEN"] = csrf
    if project_id:
        env["ANTIGRAVITY_PROJECT_ID"] = project_id
    return env


# =============================================================================
# Project registration
# =============================================================================
_project_lock = threading.Lock()


def _folder_uri(folder: Path) -> str:
    if IS_WINDOWS:                    # file:///C:/Users/... (UNTESTED against Antigravity on Windows)
        return folder.as_uri()
    return "file://" + str(folder)


def ensure_project(folder: Optional[Path] = None, name: str = "Nexus") -> str:
    """Return the Antigravity project id for the Nexus folder, registering it once."""
    folder = (folder or nexus_folder()).resolve()
    uri = _folder_uri(folder)
    cfg_path = state_dir() / "config.json"
    with _project_lock:
        cfg = _read_json(cfg_path) or {}
        pid = (cfg.get("projects") or {}).get(uri)
        if pid and (projects_dir() / f"{pid}.json").exists():
            return pid
        # Reuse a project that already points exactly at this folder.
        pid = None
        if projects_dir().is_dir():
            for p in sorted(projects_dir().glob("*.json")):
                d = _read_json(p) or {}
                res = ((d.get("projectResources") or {}).get("resources")) or []
                if any(r.get("folderUri", "").rstrip("/") == uri for r in res if isinstance(r, dict)):
                    pid = d.get("id") or p.stem
                    break
        if not pid:
            pid = str(uuid.uuid4())
            target = projects_dir() / f"{pid}.json"
            if target.exists():                                 # vanishingly unlikely; never clobber
                shutil.copy2(target, target.with_suffix(f".json.bak-{int(time.time())}"))
            _write_json(target, {
                "id": pid, "name": name,
                "projectResources": {"resources": [{"folderUri": uri}]},
                "permissionGrants": {"permissionGrants": {"allow": []}},
                "settings": {},
                "isWorkspaceOnly": False,
            })
        cfg.setdefault("projects", {})[uri] = pid
        cfg["project_id"] = pid
        _write_json(cfg_path, cfg)
        return pid


# =============================================================================
# Session -> conversation map
# =============================================================================
_map_lock = threading.Lock()
_session_locks: Dict[str, threading.Lock] = {}


def _sessions_path() -> Path:
    return state_dir() / "sessions.json"


def get_conversation(session_id: str) -> Optional[str]:
    with _map_lock:
        return ((_read_json(_sessions_path()) or {}).get(session_id) or {}).get("conversation_id")


def set_conversation(session_id: str, conversation_id: str, **meta) -> None:
    with _map_lock:
        data = _read_json(_sessions_path()) or {}
        entry = data.get(session_id) or {"created_at": _now()}
        entry.update(conversation_id=conversation_id, updated_at=_now(), **meta)
        data[session_id] = entry
        _write_json(_sessions_path(), data)


def session_lock(session_id: str) -> threading.Lock:
    with _map_lock:
        return _session_locks.setdefault(session_id, threading.Lock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =============================================================================
# agentapi
# =============================================================================
_CONN_ERR = re.compile(r"connection refused|dial tcp|no such host|EOF|tls|http2|malformed|"
                       r"unavailable|transport|server gave HTTP response", re.I)
_AUTH_ERR = re.compile(r"unauthenticated|not logged in|sign in|login|oauth|permission denied|csrf", re.I)


def _single_line(text: str, n: int = 80) -> str:
    t = " ".join(str(text or "").split())
    return (t[: n - 1] + "…") if len(t) > n else (t or "Nexus chat")


def run_agentapi(args: List[str], *, project_id: Optional[str], timeout: float = 60,
                 runner: Callable = subprocess.run) -> dict:
    """Run one agentapi command, trying each discovered port until one answers."""
    info = discover()
    last_err = "no address"
    for addr in info["addresses"]:
        try:
            proc = runner(cli_path() + args, capture_output=True, text=True, timeout=timeout,
                          env=sanitized_env(addr, info["csrf"], project_id), stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise AntigravityError(f"agentapi {args[0]} timed out after {timeout:.0f}s")
        raw = (proc.stdout or "").strip()
        try:
            data = json.loads(raw[raw.index("{"):]) if "{" in raw else {}
        except ValueError:
            data = {}
        err = data.get("error") or ("" if proc.returncode == 0 else (proc.stderr or raw or f"exit {proc.returncode}"))
        if not err:
            with _cache_lock:
                _good_address[info["csrf"]] = addr
            return data.get("response") or {}
        last_err = str(err).strip()
        if _CONN_ERR.search(last_err) and not _AUTH_ERR.search(last_err):
            continue                                            # wrong port: nothing was created
        break
    if _AUTH_ERR.search(last_err):
        raise AntigravityError(f"Antigravity refused the request — is the app signed in? ({last_err[:300]})")
    raise AntigravityError(f"agentapi {args[0]} failed: {last_err[:400]}")


def new_conversation(prompt: str, *, title: str, model: Optional[str], project_id: str,
                     runner: Callable = subprocess.run) -> str:
    args = ["new-conversation", f"--title={_single_line(title)}"]
    if model:
        if model not in MODELS:
            raise ValueError(f"model must be one of {MODELS}")
        args.append(f"--model={model}")
    resp = run_agentapi(args + [prompt], project_id=project_id, runner=runner)
    cid = (resp.get("newConversation") or {}).get("conversationId")
    if not cid:
        raise AntigravityError(f"agentapi new-conversation returned no conversationId: {json.dumps(resp)[:300]}")
    return cid


def send_message(conversation_id: str, message: str, *, project_id: str,
                 runner: Callable = subprocess.run) -> dict:
    return run_agentapi(["send-message", conversation_id, message], project_id=project_id, runner=runner)


# =============================================================================
# Transcript
# =============================================================================
_FENCE_JSON = re.compile(r"```(?:json|tool_code|tool_call)?\s*\n?\s*[\[{].*?[\]}]\s*```", re.S)


def clean_prose(text: str) -> str:
    """Drop JSON tool-call blocks; keep the human-readable answer."""
    t = _FENCE_JSON.sub("", text or "")
    lines = []
    for ln in t.splitlines():
        s = ln.strip()
        if s.startswith("{") and s.endswith("}") and ('"name"' in s or '"tool' in s or '"args"' in s):
            try:
                json.loads(s)
                continue
            except ValueError:
                pass
        lines.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def read_steps(path: Path, offset: int = 0) -> Tuple[List[dict], int]:
    """Complete JSON lines from byte ``offset``; returns (steps, new_offset)."""
    try:
        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return [], offset
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    steps = []
    for ln in chunk[: end + 1].splitlines():
        try:
            steps.append(json.loads(ln))
        except ValueError:
            continue
    return steps, offset + end + 1


def transcript_size(conversation_id: str) -> int:
    try:
        return transcript_path(conversation_id).stat().st_size
    except OSError:
        return 0


def tail_answer(conversation_id: str, offset: int = 0, *, timeout: float = DEFAULT_TIMEOUT,
                settle: float = SETTLE_S, poll: float = POLL_S,
                sleep: Callable = time.sleep, clock: Callable = time.monotonic,
                keep_tool_json: bool = False) -> Iterator[dict]:
    """Yield {"type": "text", "text": ...} per finished PLANNER_RESPONSE after ``offset``,
    then {"type": "done"}; raises TimeoutError if nothing completes in time.

    Completion: a DONE planner step that has prose and no tool calls, followed by
    ``settle`` seconds with no new working steps (CHECKPOINT etc. are ignored).
    ``keep_tool_json=True`` returns the text untouched (the Nexus brain router parses
    <tool_call> JSON itself; clean_prose would delete it)."""
    path = transcript_path(conversation_id)
    deadline = clock() + timeout
    final_at = None
    seen = set()
    while True:
        steps, offset = read_steps(path, offset)
        for st in steps:
            typ = st.get("type")
            if typ in PASSIVE_STEPS:
                continue                    # bookkeeping written after the answer; not more work
            if typ != "PLANNER_RESPONSE" or st.get("status") != "DONE":
                final_at = None             # a tool step / running response: the turn goes on
                continue
            key = st.get("step_index")
            if key in seen:
                continue
            seen.add(key)
            text = (st.get("content") or "").strip() if keep_tool_json else clean_prose(st.get("content") or "")
            if text:
                yield {"type": "text", "text": text, "step": key}
            final_at = clock() if (text and not st.get("tool_calls")) else None
        if final_at is not None and clock() - final_at >= settle:
            yield {"type": "done"}
            return
        if clock() >= deadline:
            if seen:
                yield {"type": "done", "partial": True}
                return
            raise TimeoutError(f"Antigravity did not answer within {timeout:.0f}s "
                               f"(conversation {conversation_id})")
        sleep(poll)


# =============================================================================
# Public API
# =============================================================================
def _log(**kw) -> None:
    try:
        sys.path.insert(0, str(ROOT / "agent"))
        import activity_log
        activity_log.record(agent="antigravity_bridge", brain="antigravity", action="brain_chat",
                            platform="", **kw)
    except Exception:                                           # noqa: BLE001
        pass


def chat_stream(message: str, session_id: str, model: Optional[str] = None, *,
                timeout: float = DEFAULT_TIMEOUT, runner: Callable = subprocess.run,
                **tail_kw) -> Iterator[dict]:
    """Send ``message`` in the Antigravity conversation for ``session_id`` and stream the reply.

    Yields dicts: {"type":"meta","conversation_id","new"}, {"type":"text","text"}, {"type":"done"}.
    Raises AntigravityError / TimeoutError / ValueError."""
    message = str(message or "").strip()
    if not message:
        raise ValueError("message is empty")
    if not re.match(r"^[A-Za-z0-9_.:-]{1,128}$", str(session_id or "")):
        raise ValueError("invalid session_id")
    started = time.monotonic()
    cid = None
    status = "failed"
    err = None
    lock = session_lock(session_id)
    try:
        # The lock covers create/send *and* the reply, so a duplicate request for the
        # same session waits and then becomes a follow-up instead of a second conversation.
        if not lock.acquire(timeout=timeout):
            raise AntigravityError("another request for this session is still running")
        try:
            project_id = ensure_project()
            cid = get_conversation(session_id)
            new = cid is None
            if new:
                cid = new_conversation(message, title=message, model=model, project_id=project_id, runner=runner)
                set_conversation(session_id, cid, project_id=project_id, model=model)
                offset = 0
            else:
                offset = transcript_size(cid)
                send_message(cid, message, project_id=project_id, runner=runner)
                set_conversation(session_id, cid)
            yield {"type": "meta", "conversation_id": cid, "new": new, "session_id": session_id}
            for ev in tail_answer(cid, offset, timeout=timeout, **tail_kw):
                yield ev
            status = "ok"
        finally:
            lock.release()
    except Exception as e:
        err = str(e)
        raise
    finally:
        _log(status=status, content=_single_line(message, 200), error=err, conversation_id=cid,
             session_id=session_id, model=model or "default",
             duration_s=round(time.monotonic() - started, 2))


def chat(message: str, session_id: str, model: Optional[str] = None, **kw) -> dict:
    parts, cid = [], None
    for ev in chat_stream(message, session_id, model, **kw):
        if ev["type"] == "meta":
            cid = ev["conversation_id"]
        elif ev["type"] == "text":
            parts.append(ev["text"])
    return {"conversation_id": cid, "text": "\n\n".join(parts)}


async def achat_stream(message: str, session_id: str, model: Optional[str] = None, **kw):
    """Async wrapper: runs the blocking generator in a worker thread."""
    import asyncio
    loop = asyncio.get_event_loop()
    gen = chat_stream(message, session_id, model, **kw)
    sentinel = object()
    while True:
        ev = await loop.run_in_executor(None, next, gen, sentinel)
        if ev is sentinel:
            return
        yield ev


def status() -> dict:
    """Cheap availability probe (no conversation is created)."""
    try:
        info = discover()
        return {"ok": True, "available": True, "pid": info["pid"], "addresses": info["addresses"],
                "models": list(MODELS)}
    except AntigravityError as e:
        return {"ok": True, "available": False, "reason": str(e), "models": list(MODELS)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Google Antigravity brain bridge for Nexus")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    c = sub.add_parser("chat")
    c.add_argument("session_id"); c.add_argument("message")
    c.add_argument("--model", choices=MODELS)
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    try:
        for ev in chat_stream(a.message, a.session_id, a.model):
            print(json.dumps(ev, ensure_ascii=False), flush=True)
    except (AntigravityError, TimeoutError, ValueError) as e:
        print(json.dumps({"type": "error", "error": str(e)}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
