"""Opt-in runtime audit instrumentation.

Enabled only when AUDIT_MODE=1 (or truthy). When disabled, this module is a no-op.
"""

from __future__ import annotations

import atexit
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional


_TRUTHY = {"1", "true", "yes", "on"}
_TARGET_PREFIXES = ("app", "ui", "scripts")
_TLS = threading.local()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _timestamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _truthy_env(name: str) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    return value in _TRUTHY


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _relative_repo_path(path_value: str, *, root: Path) -> Optional[str]:
    if not path_value:
        return None
    with suppress(Exception):
        path = Path(path_value).resolve()
        rel = path.relative_to(root)
        return str(rel).replace("\\", "/")
    return None


def _is_target_repo_file(path_value: str, *, root: Path) -> bool:
    rel = _relative_repo_path(path_value, root=root)
    if rel is None:
        return False
    if rel == "app/audit_mode.py":
        return False
    return rel.startswith("app/") or rel.startswith("ui/") or rel.startswith("scripts/")


def _set_guard(value: bool) -> None:
    setattr(_TLS, "audit_guard", bool(value))


def _guarded() -> bool:
    return bool(getattr(_TLS, "audit_guard", False))


@dataclass
class _AuditState:
    scenario: str
    trace_jsonl_path: Path
    coverage_path: Path
    summary_path: Path
    root: Path
    started_iso: str
    started_mono: float
    started_perf: float
    handle: Any
    lock: threading.Lock
    event_count: int
    module_calls: Counter[str]
    function_calls: Counter[str]
    first_seen_functions: set[str]
    entrypoints: list[str]
    original_profile: Optional[Callable[..., Any]]
    original_sqlite_connect: Callable[..., Any]
    original_subprocess_run: Callable[..., Any]
    original_subprocess_popen: Any
    patched: bool
    finalized: bool


_STATE: Optional[_AuditState] = None


def _emit(event: str, payload: Optional[Dict[str, Any]] = None) -> None:
    state = _STATE
    if state is None or state.finalized:
        return
    if _guarded():
        return
    _set_guard(True)
    try:
        row = {
            "event": str(event),
            "ts": _now_iso(),
            "mono_s": round(time.monotonic() - state.started_mono, 6),
            "pid": os.getpid(),
            "scenario": state.scenario,
        }
        if payload:
            row.update(payload)
        line = json.dumps(row, ensure_ascii=False, sort_keys=True)
        with state.lock:
            state.handle.write(line + "\n")
            state.event_count += 1
    except Exception:
        pass
    finally:
        _set_guard(False)


def _sqlite_snippet(sql: Any) -> str:
    text = str(sql or "").strip().replace("\n", " ")
    if len(text) > 300:
        return text[:300] + "..."
    return text


def _db_path_of_connection(conn: sqlite3.Connection) -> str:
    value = getattr(conn, "_audit_db_path", None)
    if value is None:
        return "<unknown>"
    return str(value)


def _trace_sql_event(
    conn: sqlite3.Connection,
    *,
    method: str,
    sql: Any,
    started_perf: float,
    ok: bool,
    error: Optional[str] = None,
) -> None:
    _emit(
        "sqlite_query",
        {
            "method": str(method),
            "db_path": _db_path_of_connection(conn),
            "sql": _sqlite_snippet(sql),
            "duration_ms": round((time.perf_counter() - started_perf) * 1000.0, 3),
            "ok": bool(ok),
            "error": error,
        },
    )


class _AuditSQLiteCursor(sqlite3.Cursor):
    def execute(self, sql: Any, parameters: Any = None) -> "_AuditSQLiteCursor":
        started = time.perf_counter()
        conn = self.connection
        try:
            if parameters is None:
                out = super().execute(sql)
            else:
                out = super().execute(sql, parameters)
            _trace_sql_event(conn, method="cursor.execute", sql=sql, started_perf=started, ok=True)
            return out
        except Exception as exc:
            _trace_sql_event(
                conn,
                method="cursor.execute",
                sql=sql,
                started_perf=started,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def executemany(self, sql: Any, seq_of_parameters: Any) -> "_AuditSQLiteCursor":
        started = time.perf_counter()
        conn = self.connection
        try:
            out = super().executemany(sql, seq_of_parameters)
            _trace_sql_event(conn, method="cursor.executemany", sql=sql, started_perf=started, ok=True)
            return out
        except Exception as exc:
            _trace_sql_event(
                conn,
                method="cursor.executemany",
                sql=sql,
                started_perf=started,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def executescript(self, sql_script: Any) -> "_AuditSQLiteCursor":
        started = time.perf_counter()
        conn = self.connection
        try:
            out = super().executescript(sql_script)
            _trace_sql_event(conn, method="cursor.executescript", sql=sql_script, started_perf=started, ok=True)
            return out
        except Exception as exc:
            _trace_sql_event(
                conn,
                method="cursor.executescript",
                sql=sql_script,
                started_perf=started,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise


class _AuditSQLiteConnection(sqlite3.Connection):
    def cursor(self, *args: Any, **kwargs: Any) -> _AuditSQLiteCursor:
        if "factory" not in kwargs:
            kwargs["factory"] = _AuditSQLiteCursor
        return super().cursor(*args, **kwargs)

    def execute(self, sql: Any, parameters: Any = None) -> _AuditSQLiteCursor:
        started = time.perf_counter()
        try:
            if parameters is None:
                out = super().execute(sql)
            else:
                out = super().execute(sql, parameters)
            _trace_sql_event(self, method="connection.execute", sql=sql, started_perf=started, ok=True)
            return out
        except Exception as exc:
            _trace_sql_event(
                self,
                method="connection.execute",
                sql=sql,
                started_perf=started,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def executemany(self, sql: Any, parameters: Any) -> _AuditSQLiteCursor:
        started = time.perf_counter()
        try:
            out = super().executemany(sql, parameters)
            _trace_sql_event(self, method="connection.executemany", sql=sql, started_perf=started, ok=True)
            return out
        except Exception as exc:
            _trace_sql_event(
                self,
                method="connection.executemany",
                sql=sql,
                started_perf=started,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def executescript(self, sql_script: Any) -> _AuditSQLiteCursor:
        started = time.perf_counter()
        try:
            out = super().executescript(sql_script)
            _trace_sql_event(self, method="connection.executescript", sql=sql_script, started_perf=started, ok=True)
            return out
        except Exception as exc:
            _trace_sql_event(
                self,
                method="connection.executescript",
                sql=sql_script,
                started_perf=started,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise


def _patch_sqlite() -> None:
    state = _STATE
    if state is None:
        return

    def _connect_wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        db_path = "<unknown>"
        if args:
            db_path = str(args[0])
        elif "database" in kwargs:
            db_path = str(kwargs.get("database"))

        local_kwargs = dict(kwargs)
        custom_factory = "factory" in local_kwargs
        if not custom_factory:
            local_kwargs["factory"] = _AuditSQLiteConnection

        try:
            conn = state.original_sqlite_connect(*args, **local_kwargs)
            if isinstance(conn, sqlite3.Connection):
                with suppress(Exception):
                    setattr(conn, "_audit_db_path", db_path)
            _emit(
                "sqlite_connect",
                {
                    "db_path": db_path,
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "custom_factory": custom_factory,
                    "ok": True,
                },
            )
            return conn
        except Exception as exc:
            _emit(
                "sqlite_connect",
                {
                    "db_path": db_path,
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "custom_factory": custom_factory,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise

    sqlite3.connect = _connect_wrapper  # type: ignore[assignment]


def _popen_cmd_preview(args: tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
    if args:
        return str(args[0])
    if "args" in kwargs:
        return str(kwargs["args"])
    return "<unknown>"


def _patch_subprocess() -> None:
    state = _STATE
    if state is None:
        return

    def _run_wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        cmd = _popen_cmd_preview(args, kwargs)
        cwd = kwargs.get("cwd")
        try:
            result = state.original_subprocess_run(*args, **kwargs)
            _emit(
                "subprocess_run",
                {
                    "command": cmd,
                    "cwd": str(cwd) if cwd is not None else None,
                    "returncode": int(getattr(result, "returncode", 0)),
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "ok": True,
                },
            )
            return result
        except Exception as exc:
            _emit(
                "subprocess_run",
                {
                    "command": cmd,
                    "cwd": str(cwd) if cwd is not None else None,
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise

    class _AuditPopen(state.original_subprocess_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._audit_started_perf = time.perf_counter()
            self._audit_cmd = _popen_cmd_preview(args, kwargs)
            self._audit_cwd = str(kwargs.get("cwd")) if kwargs.get("cwd") is not None else None
            self._audit_done = False
            super().__init__(*args, **kwargs)
            _emit(
                "subprocess_popen_start",
                {
                    "command": self._audit_cmd,
                    "cwd": self._audit_cwd,
                    "pid_started": int(getattr(self, "pid", 0) or 0),
                },
            )

        def _audit_finalize(self) -> None:
            if self._audit_done:
                return
            returncode = getattr(self, "returncode", None)
            if returncode is None:
                return
            self._audit_done = True
            _emit(
                "subprocess_popen_end",
                {
                    "command": self._audit_cmd,
                    "cwd": self._audit_cwd,
                    "pid_started": int(getattr(self, "pid", 0) or 0),
                    "returncode": int(returncode),
                    "duration_ms": round((time.perf_counter() - self._audit_started_perf) * 1000.0, 3),
                },
            )

        def poll(self) -> Any:
            out = super().poll()
            if out is not None:
                self._audit_finalize()
            return out

        def wait(self, timeout: Any = None) -> Any:
            out = super().wait(timeout=timeout)
            self._audit_finalize()
            return out

        def communicate(self, *args: Any, **kwargs: Any) -> Any:
            out = super().communicate(*args, **kwargs)
            self._audit_finalize()
            return out

    subprocess.run = _run_wrapper  # type: ignore[assignment]
    subprocess.Popen = _AuditPopen  # type: ignore[assignment]


def _profile_callback(frame: Any, event: str, arg: Any) -> Any:
    state = _STATE
    if state is None or state.finalized:
        return state.original_profile if state is not None else None

    if event == "call":
        if _guarded():
            return state.original_profile
        filename = str(getattr(frame.f_code, "co_filename", "") or "")
        if _is_target_repo_file(filename, root=state.root):
            rel = _relative_repo_path(filename, root=state.root)
            if rel:
                state.module_calls[rel] += 1
                key = f"{rel}:{int(frame.f_code.co_firstlineno)}:{str(frame.f_code.co_name)}"
                state.function_calls[key] += 1
                if key not in state.first_seen_functions:
                    state.first_seen_functions.add(key)
                    _emit(
                        "py_function_first_call",
                        {
                            "module": rel,
                            "function": str(frame.f_code.co_name),
                            "line": int(frame.f_code.co_firstlineno),
                        },
                    )

    if state.original_profile is not None:
        with suppress(Exception):
            return state.original_profile(frame, event, arg)
    return None


def _patch_runtime() -> None:
    state = _STATE
    if state is None or state.patched:
        return
    _patch_sqlite()
    _patch_subprocess()
    sys.setprofile(_profile_callback)
    threading.setprofile(_profile_callback)
    state.patched = True


def _restore_runtime() -> None:
    state = _STATE
    if state is None:
        return
    with suppress(Exception):
        sqlite3.connect = state.original_sqlite_connect  # type: ignore[assignment]
    with suppress(Exception):
        subprocess.run = state.original_subprocess_run  # type: ignore[assignment]
    with suppress(Exception):
        subprocess.Popen = state.original_subprocess_popen  # type: ignore[assignment]
    with suppress(Exception):
        sys.setprofile(state.original_profile)
    with suppress(Exception):
        threading.setprofile(state.original_profile)


def _write_summary_files() -> None:
    state = _STATE
    if state is None or state.finalized:
        return
    state.finalized = True
    runtime_s = time.perf_counter() - state.started_perf

    coverage_payload = {
        "generated_at": _now_iso(),
        "scenario": state.scenario,
        "trace_jsonl": str(state.trace_jsonl_path),
        "module_call_counts": dict(sorted(state.module_calls.items())),
        "function_call_counts": dict(sorted(state.function_calls.items())),
    }
    summary_payload = {
        "generated_at": _now_iso(),
        "scenario": state.scenario,
        "trace_jsonl": str(state.trace_jsonl_path),
        "coverage_json": str(state.coverage_path),
        "runtime_seconds": round(runtime_s, 6),
        "event_count": int(state.event_count),
        "pid": os.getpid(),
        "argv": list(sys.argv),
        "entrypoints": list(state.entrypoints),
    }

    with suppress(Exception):
        state.coverage_path.parent.mkdir(parents=True, exist_ok=True)
        state.coverage_path.write_text(
            json.dumps(coverage_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    with suppress(Exception):
        state.summary_path.parent.mkdir(parents=True, exist_ok=True)
        state.summary_path.write_text(
            json.dumps(summary_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    with suppress(Exception):
        state.handle.flush()
    with suppress(Exception):
        state.handle.close()


def flush_audit_mode() -> None:
    """Flush trace summary files if AUDIT_MODE is active."""
    _write_summary_files()


def _shutdown() -> None:
    _write_summary_files()
    _restore_runtime()


def is_audit_mode_enabled() -> bool:
    return _STATE is not None and not _STATE.finalized


def enable_audit_mode(*, entrypoint: str = "") -> bool:
    """Enable runtime audit instrumentation if AUDIT_MODE is truthy."""

    global _STATE

    if _STATE is not None:
        if entrypoint and entrypoint not in _STATE.entrypoints:
            _STATE.entrypoints.append(entrypoint)
            _emit("entrypoint", {"value": entrypoint})
        return True

    if not _truthy_env("AUDIT_MODE"):
        return False

    scenario = str(os.environ.get("AUDIT_SCENARIO", "")).strip() or "default"
    scenario_clean = "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in scenario)
    trace_dir = str(os.environ.get("AUDIT_TRACE_DIR", "")).strip() or "audit/run_traces"
    trace_file_env = str(os.environ.get("AUDIT_TRACE_FILE", "")).strip()
    root = _repo_root()
    stamp = _timestamp_token()

    if trace_file_env:
        trace_jsonl_path = Path(trace_file_env).expanduser()
        if not trace_jsonl_path.is_absolute():
            trace_jsonl_path = (Path.cwd() / trace_jsonl_path).resolve()
        base = trace_jsonl_path.with_suffix("")
        coverage_path = Path(str(base) + ".coverage.json")
        summary_path = Path(str(base) + ".summary.json")
    else:
        trace_root = Path(trace_dir).expanduser()
        if not trace_root.is_absolute():
            trace_root = (Path.cwd() / trace_root).resolve()
        scenario_dir = trace_root / scenario_clean
        trace_jsonl_path = scenario_dir / f"{stamp}.jsonl"
        coverage_path = scenario_dir / f"{stamp}.coverage.json"
        summary_path = scenario_dir / f"{stamp}.summary.json"

    trace_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    handle = trace_jsonl_path.open("a", encoding="utf-8")

    _STATE = _AuditState(
        scenario=scenario_clean,
        trace_jsonl_path=trace_jsonl_path,
        coverage_path=coverage_path,
        summary_path=summary_path,
        root=root,
        started_iso=_now_iso(),
        started_mono=time.monotonic(),
        started_perf=time.perf_counter(),
        handle=handle,
        lock=threading.Lock(),
        event_count=0,
        module_calls=Counter(),
        function_calls=Counter(),
        first_seen_functions=set(),
        entrypoints=[],
        original_profile=sys.getprofile(),
        original_sqlite_connect=sqlite3.connect,
        original_subprocess_run=subprocess.run,
        original_subprocess_popen=subprocess.Popen,
        patched=False,
        finalized=False,
    )

    _patch_runtime()
    atexit.register(_shutdown)

    if entrypoint:
        _STATE.entrypoints.append(entrypoint)
    _emit(
        "process_start",
        {
            "entrypoint": entrypoint or None,
            "trace_jsonl": str(trace_jsonl_path),
            "coverage_json": str(coverage_path),
            "summary_json": str(summary_path),
            "cwd": str(Path.cwd()),
            "argv": list(sys.argv),
            "python": sys.executable,
        },
    )
    if entrypoint:
        _emit("entrypoint", {"value": entrypoint})
    return True


def _reset_for_tests() -> None:
    """Reset global audit state. Test-only helper."""
    global _STATE
    _shutdown()
    _STATE = None


__all__ = [
    "enable_audit_mode",
    "flush_audit_mode",
    "is_audit_mode_enabled",
    "_reset_for_tests",
]

