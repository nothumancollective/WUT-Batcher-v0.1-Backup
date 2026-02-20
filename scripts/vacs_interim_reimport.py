"""Interim helper: reopen VACS and retrigger AKABAK -> VACS transfer via F7.

This script is intentionally standalone (not integrated into drivers/runner yet).
It is for fast VACS-driver iteration without re-running a full simulation each time.
"""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from pywinauto import Application, Desktop

# Allow standalone execution via "python scripts/..." by adding repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.ui_contracts.window_signatures import AKABAK_MAIN_WINDOW, VACS_MAIN_WINDOW


WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_F7 = 0x76

GRAPH_KEYWORDS = ("graph", "impedance", "spl", "phase", "radiation", "polar", "directivity")
GRAPH_WINDOW_CLASSES = {"TForm_DatGraph", "TForm_DatContour"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_text(control: Any) -> str:
    try:
        return str(control.window_text() or "").strip()
    except Exception:
        try:
            return str(getattr(control.element_info, "name", "") or "").strip()
        except Exception:
            return ""


def _window_handle(control: Any) -> int:
    try:
        return int(getattr(control.element_info, "handle", 0) or 0)
    except Exception:
        return 0


def _collect_window_rows(pid: int) -> List[Any]:
    if int(pid or 0) <= 0:
        return []
    try:
        return list(Desktop(backend="uia").windows(process=int(pid)))
    except Exception:
        return []


def _collect_all_windows() -> List[Any]:
    try:
        return list(Desktop(backend="uia").windows())
    except Exception:
        return []


def _find_first_window(
    pid: int,
    *,
    class_name_regex: Optional[str] = None,
    title_regex: Optional[str] = None,
) -> Optional[Any]:
    for window in _collect_window_rows(pid):
        try:
            info = window.element_info
            class_name = str(getattr(info, "class_name", "") or "")
            title = str(getattr(info, "name", "") or "")
        except Exception:
            continue
        if class_name_regex and not re.search(class_name_regex, class_name, re.IGNORECASE):
            continue
        if title_regex and not re.search(title_regex, title, re.IGNORECASE):
            continue
        return window
    return None


def _find_main_window(pid: int, signature) -> Optional[Any]:
    return _find_first_window(
        pid,
        class_name_regex=signature.class_name_regex,
        title_regex=signature.title_regex,
    )


def _find_any_vacs_main_window_global() -> Optional[Any]:
    for window in _collect_all_windows():
        try:
            info = window.element_info
            class_name = str(getattr(info, "class_name", "") or "")
            title = str(getattr(info, "name", "") or "")
            pid = int(getattr(info, "process_id", 0) or 0)
        except Exception:
            continue
        if pid <= 0:
            continue
        if VACS_MAIN_WINDOW.class_name_regex and not re.search(VACS_MAIN_WINDOW.class_name_regex, class_name, re.IGNORECASE):
            continue
        if VACS_MAIN_WINDOW.title_regex and not re.search(VACS_MAIN_WINDOW.title_regex, title, re.IGNORECASE):
            continue
        return window
    return None


def _list_vacs_main_window_pids_global() -> List[int]:
    rows: List[int] = []
    for window in _collect_all_windows():
        try:
            info = window.element_info
            class_name = str(getattr(info, "class_name", "") or "")
            title = str(getattr(info, "name", "") or "")
            pid = int(getattr(info, "process_id", 0) or 0)
        except Exception:
            continue
        if pid <= 0:
            continue
        if VACS_MAIN_WINDOW.class_name_regex and not re.search(VACS_MAIN_WINDOW.class_name_regex, class_name, re.IGNORECASE):
            continue
        if VACS_MAIN_WINDOW.title_regex and not re.search(VACS_MAIN_WINDOW.title_regex, title, re.IGNORECASE):
            continue
        rows.append(pid)
    return sorted(set(rows))


def _connect_existing(executable: str) -> Tuple[Application, int]:
    app = Application(backend="uia")
    app.connect(path=str(Path(executable).resolve()))
    pid = int(app.process)
    return app, pid


def _kill_vacs_processes() -> None:
    for image in ("vacsviewer_32.exe", "vacsviewer.exe"):
        subprocess.run(["taskkill", "/IM", image, "/T", "/F"], capture_output=True, text=True)


def _connect_existing_vacs(executable: str, timeout_s: int) -> Tuple[Application, int]:
    exe = str(Path(executable).resolve())
    app = Application(backend="uia")
    # Preferred attach by configured executable path.
    try:
        app.connect(path=exe)
        pid = int(app.process)
        deadline = time.perf_counter() + max(3.0, float(timeout_s))
        while time.perf_counter() < deadline:
            if _find_main_window(pid, VACS_MAIN_WINDOW) is not None:
                return app, pid
            time.sleep(0.2)
    except Exception:
        pass

    # Fallback: discover any VACS main window globally and attach by PID.
    deadline = time.perf_counter() + max(3.0, float(timeout_s))
    while time.perf_counter() < deadline:
        window = _find_any_vacs_main_window_global()
        if window is not None:
            pid = int(getattr(window.element_info, "process_id", 0) or 0)
            if pid > 0:
                app = Application(backend="uia")
                app.connect(process=pid)
                return app, pid
        time.sleep(0.2)
    raise RuntimeError("VACS process/window not found for attach.")


def _open_vacs_via_akabak_menu(akabak_main: Any, timeout_s: int) -> Dict[str, Any]:
    hwnd = _window_handle(akabak_main)
    if hwnd <= 0:
        raise RuntimeError("AKABAK main window handle unavailable for Open VACS menu action.")
    try:
        win32_main = Desktop(backend="win32").window(handle=hwnd)
    except Exception as exc:
        raise RuntimeError(f"Unable to bind AKABAK main window via win32 backend: {exc!r}") from exc

    menu_paths = [
        "Options->Open &VACS...",
        "Options->Open &VACS",
        "Options->Open VACS...",
        "Options->Open VACS",
        "Options->Open Vacs...",
        "Options->Open Vacs",
    ]
    errors: List[str] = []
    for _ in range(3):
        for path in menu_paths:
            try:
                win32_main.menu_select(path)
                time.sleep(0.2)
                return {"status": "ok", "method": "menu_select", "menu_path": path, "errors": errors}
            except Exception as exc:
                errors.append(f"{path}: {exc!r}")
                continue
        # If menu is disabled, there is usually a modal blocking the main menu.
        try:
            akabak_pid = int(getattr(akabak_main.element_info, "process_id", 0) or 0)
        except Exception:
            akabak_pid = 0
        if akabak_pid > 0:
            dialog = _find_any_akabak_dialog(akabak_pid, akabak_main=akabak_main)
            if dialog is not None:
                _press_dialog_yes(dialog["window"])
                time.sleep(0.25)
                continue

    # Keyboard fallback through menu bar as last resort.
    try:
        akabak_main.set_focus()
        akabak_main.type_keys("%o", set_foreground=True)
        akabak_main.type_keys("v", set_foreground=True)
        time.sleep(0.2)
        return {"status": "ok", "method": "keyboard_menu_fallback", "errors": errors}
    except Exception as exc:
        errors.append(f"keyboard_fallback: {exc!r}")
    raise RuntimeError(f"Failed to trigger AKABAK menu Open VACS. Attempts: {errors}")


def _connect_vacs_via_akabak(
    *,
    vacs_executable: str,
    akabak_main: Any,
    timeout_s: int,
    force_open: bool,
    require_akabak_launch: bool,
    allow_existing_on_com_error: bool,
) -> Tuple[int, bool, Dict[str, Any]]:
    # Try attach first unless forced open is requested.
    if not force_open:
        try:
            _, pid = _connect_existing_vacs(vacs_executable, timeout_s=max(3, int(timeout_s / 2)))
            return pid, False, {"status": "attached_existing"}
        except Exception:
            pass

    baseline_vacs_pids = set(_list_vacs_main_window_pids_global())
    open_meta = _open_vacs_via_akabak_menu(akabak_main, timeout_s=timeout_s)
    deadline = time.perf_counter() + max(5.0, float(timeout_s))
    last_error = ""
    while time.perf_counter() < deadline:
        try:
            _, pid = _connect_existing_vacs(vacs_executable, timeout_s=2)
            if require_akabak_launch and int(pid) in baseline_vacs_pids:
                last_error = (
                    f"detected_existing_vacs_pid={pid} (requires AKABAK-launched instance); "
                    "waiting for a freshly launched VACS process"
                )
                time.sleep(0.2)
                continue
            if int(pid) in baseline_vacs_pids:
                return pid, False, {**open_meta, "status": "attached_existing_after_menu_attempt"}
            return pid, True, {**open_meta, "status": "opened_via_akabak"}
        except Exception as exc:
            last_error = repr(exc)
            try:
                akabak_pid = int(getattr(akabak_main.element_info, "process_id", 0) or 0)
            except Exception:
                akabak_pid = 0
            if akabak_pid > 0:
                modal = _find_any_akabak_dialog(akabak_pid, akabak_main=akabak_main)
                if modal is not None and _is_vacs_com_missing_dialog(str(modal.get("message", ""))):
                    if allow_existing_on_com_error:
                        try:
                            _, pid = _connect_existing_vacs(vacs_executable, timeout_s=max(3, int(timeout_s / 2)))
                            return pid, False, {
                                **open_meta,
                                "status": "attached_existing_after_com_error",
                                "com_error_message": str(modal.get("message", "")),
                            }
                        except Exception:
                            pass
                    raise RuntimeError(
                        "vacs_com_registration_missing: "
                        + str(modal.get("message", "")).strip().replace("\r\n", " ")
                    )
            time.sleep(0.25)
    raise RuntimeError(
        "VACS did not appear as AKABAK-launched instance after menu open. "
        f"last_error={last_error}"
    )


def _dialog_message(window: Any) -> str:
    chunks: List[str] = []
    try:
        for child in window.descendants():
            try:
                info = child.element_info
                control_type = str(getattr(info, "control_type", "") or "")
            except Exception:
                continue
            if control_type not in {"Text", "Document"}:
                continue
            text = _window_text(child)
            if text:
                chunks.append(text)
    except Exception:
        pass
    if chunks:
        return " | ".join(chunks[:6])
    return _window_text(window)


def _find_edge_length_dialog(akabak_pid: int, akabak_main: Optional[Any] = None) -> Optional[Any]:
    if akabak_main is not None:
        try:
            for child in akabak_main.descendants():
                info = child.element_info
                class_name = str(getattr(info, "class_name", "") or "")
                control_type = str(getattr(info, "control_type", "") or "")
                if not re.search(r"(#32770|Dialog)", class_name, re.IGNORECASE):
                    continue
                if control_type != "Window":
                    continue
                message = _dialog_message(child).lower()
                if "edge" in message and "length" in message and ("proceed" in message or "specified" in message):
                    return child
        except Exception:
            pass
    for window in _collect_window_rows(akabak_pid):
        try:
            class_name = str(getattr(window.element_info, "class_name", "") or "")
        except Exception:
            continue
        if not re.search(r"(#32770|Dialog)", class_name, re.IGNORECASE):
            continue
        message = _dialog_message(window).lower()
        if "edge" in message and "length" in message and ("proceed" in message or "specified" in message):
            return window
    return None


def _find_any_akabak_dialog(akabak_pid: int, akabak_main: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    candidates: List[Any] = []
    if akabak_main is not None:
        try:
            candidates.extend(list(akabak_main.descendants()))
        except Exception:
            pass
    candidates.extend(_collect_window_rows(akabak_pid))
    for window in candidates:
        try:
            info = window.element_info
            class_name = str(getattr(info, "class_name", "") or "")
            control_type = str(getattr(info, "control_type", "") or "")
        except Exception:
            continue
        if not re.search(r"(#32770|Dialog)", class_name, re.IGNORECASE):
            continue
        if control_type != "Window":
            continue
        message = _dialog_message(window)
        if not message.strip():
            continue
        return {"window": window, "message": message, "class_name": class_name, "title": _window_text(window)}
    return None


def _is_vacs_com_missing_dialog(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "cannot locate vacs.exe" in text
        or "cannot locate vacsviewer.exe" in text
        or ("regserver" in text and "vacs" in text)
    )


def _press_dialog_yes(dialog: Any) -> Dict[str, Any]:
    for caption in ("Yes", "Ja", "OK", "Ok", "Continue", "Fortfahren"):
        try:
            button = dialog.child_window(title=caption, control_type="Button")
            if button.exists(timeout=0.2):
                try:
                    button.invoke()
                    return {"status": "ok", "method": "invoke", "caption": caption}
                except Exception:
                    button.click()
                    return {"status": "ok", "method": "click", "caption": caption}
        except Exception:
            continue
    # Robust fallback: scan descendant buttons by visible text.
    try:
        for button in dialog.descendants():
            info = button.element_info
            if str(getattr(info, "control_type", "") or "") != "Button":
                continue
            label = _window_text(button).strip().lower()
            if label in {"yes", "ja", "ok", "continue", "fortfahren"}:
                try:
                    button.invoke()
                    return {"status": "ok", "method": "invoke_descendant", "caption": label}
                except Exception:
                    button.click()
                    return {"status": "ok", "method": "click_descendant", "caption": label}
    except Exception:
        pass
    try:
        dialog.type_keys("{ENTER}")
        return {"status": "ok", "method": "enter_fallback", "caption": ""}
    except Exception as exc:
        return {"status": "error", "error": repr(exc)}


def _press_dialog_no(dialog: Any) -> Dict[str, Any]:
    for caption in ("No", "Nein", "Don't Save", "Nicht speichern", "N"):
        try:
            button = dialog.child_window(title=caption, control_type="Button")
            if button.exists(timeout=0.2):
                try:
                    button.invoke()
                    return {"status": "ok", "method": "invoke", "caption": caption}
                except Exception:
                    button.click()
                    return {"status": "ok", "method": "click", "caption": caption}
        except Exception:
            continue
    # Fallback: pick the second button if present (common [Yes, No, Cancel] layout).
    try:
        buttons = [b for b in dialog.descendants() if str(getattr(b.element_info, "control_type", "") or "") == "Button"]
        if len(buttons) >= 2:
            try:
                buttons[1].invoke()
                return {"status": "ok", "method": "invoke_index_1", "caption": _window_text(buttons[1])}
            except Exception:
                buttons[1].click()
                return {"status": "ok", "method": "click_index_1", "caption": _window_text(buttons[1])}
    except Exception:
        pass
    try:
        dialog.type_keys("%n")
        return {"status": "ok", "method": "alt_n", "caption": "No"}
    except Exception as exc:
        return {"status": "error", "error": repr(exc)}


def _find_embedded_save_prompt(main_window: Any) -> Optional[Any]:
    try:
        for child in main_window.descendants():
            info = child.element_info
            class_name = str(getattr(info, "class_name", "") or "")
            control_type = str(getattr(info, "control_type", "") or "")
            if not re.search(r"(#32770|Dialog)", class_name, re.IGNORECASE):
                continue
            if control_type != "Window":
                continue
            msg = _dialog_message(child).lower()
            if "save" in msg or "speichern" in msg:
                return child
    except Exception:
        return None
    return None


def _vacs_metrics(vacs_pid: int) -> Dict[str, Any]:
    max_controls = 0
    max_hits = 0
    rows: List[Dict[str, Any]] = []
    for window in _collect_window_rows(vacs_pid)[:5]:
        title = _window_text(window)
        class_name = str(getattr(window.element_info, "class_name", "") or "")
        controls = 0
        hits = 0
        try:
            descendants = list(window.descendants())
            controls = len(descendants)
            for control in descendants[:1500]:
                token = _window_text(control).lower()
                if token and any(keyword in token for keyword in GRAPH_KEYWORDS):
                    hits += 1
        except Exception:
            pass
        max_controls = max(max_controls, controls)
        max_hits = max(max_hits, hits)
        rows.append({"title": title, "class_name": class_name, "controls_count": controls, "graph_keyword_hits": hits})
    graph_window_count = 0
    try:
        for window in Desktop(backend="win32").windows(process=int(vacs_pid)):
            try:
                class_name = str(getattr(window.element_info, "class_name", "") or "")
            except Exception:
                continue
            if class_name in GRAPH_WINDOW_CLASSES:
                graph_window_count += 1
    except Exception:
        graph_window_count = 0

    return {
        "pid": int(vacs_pid),
        "max_controls_count": int(max_controls),
        "max_graph_keyword_hits": int(max_hits),
        "graph_window_count": int(graph_window_count),
        "windows": rows,
    }


def _close_vacs_without_saving(vacs_pid: int) -> Dict[str, Any]:
    result: Dict[str, Any] = {"status": "unknown"}
    main = _find_main_window(vacs_pid, VACS_MAIN_WINDOW)
    if main is None:
        return {"status": "already_closed"}
    main_hwnd = _window_handle(main)
    try:
        main.close()
        result = {"status": "close_requested"}
    except Exception as exc:
        return {"status": "close_error", "error": repr(exc)}

    deadline = time.perf_counter() + 10.0
    alt_f4_sent = False
    wm_close_sent = False
    while time.perf_counter() < deadline:
        prompt: Optional[Any] = None
        embedded_prompt = _find_embedded_save_prompt(main)
        if embedded_prompt is not None:
            prompt = embedded_prompt
        candidate_windows = list(_collect_window_rows(vacs_pid))
        candidate_windows.extend(_collect_all_windows())
        if prompt is None:
            for window in candidate_windows:
                class_name = str(getattr(window.element_info, "class_name", "") or "")
                if not re.search(r"(#32770|Dialog)", class_name, re.IGNORECASE):
                    continue
                msg = _dialog_message(window).lower()
                if "save" in msg or "speichern" in msg:
                    prompt = window
                    break
        if prompt is not None:
            action = _press_dialog_no(prompt)
            result["save_prompt_action"] = action
        if not _collect_window_rows(vacs_pid):
            return {**result, "status": "closed"}
        if not alt_f4_sent:
            try:
                main.set_focus()
                main.type_keys("%{F4}", set_foreground=True)
                alt_f4_sent = True
            except Exception:
                pass
        elif not wm_close_sent and main_hwnd > 0:
            ctypes.windll.user32.SendMessageW(main_hwnd, 0x0010, 0, 0)  # WM_CLOSE
            wm_close_sent = True
        time.sleep(0.2)
    return {**result, "status": "close_timeout"}


def run_interim(args: argparse.Namespace) -> Dict[str, Any]:
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = out_dir / f"vacs_interim_reimport_{run_id}.json"

    timeline: List[Dict[str, Any]] = []

    def log(event: str, **payload: Any) -> None:
        timeline.append({"time": _now_iso(), "event": event, "payload": payload})

    akabak_exe = str(Path(args.akabak_exe).resolve())
    vacs_exe = str(Path(args.vacs_exe).resolve())

    try:
        _, akabak_pid = _connect_existing(akabak_exe)
    except Exception as exc:
        summary = {
            "ok": False,
            "error": f"AKABAK not attachable (must already be running): {exc!r}",
            "timeline": timeline,
        }
        log_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return summary

    log("akabak_connected", pid=akabak_pid, exe=akabak_exe)
    akabak_main = _find_main_window(akabak_pid, AKABAK_MAIN_WINDOW)
    if akabak_main is None:
        summary = {
            "ok": False,
            "error": "AKABAK main window not found.",
            "timeline": timeline,
        }
        log_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return summary

    force_open_initial = not bool(args.allow_existing_vacs)
    try:
        if bool(args.open_vacs_via_akabak):
            vacs_pid, vacs_started, vacs_open_meta = _connect_vacs_via_akabak(
                vacs_executable=vacs_exe,
                akabak_main=akabak_main,
                timeout_s=args.startup_timeout_s,
                force_open=True,
                require_akabak_launch=False,
                allow_existing_on_com_error=bool(args.allow_existing_vacs),
            )
        else:
            _, vacs_pid = _connect_existing_vacs(vacs_exe, timeout_s=args.startup_timeout_s)
            vacs_started = False
            vacs_open_meta = {"status": "attached_existing_only"}
    except Exception as exc:
        summary = {
            "ok": False,
            "error": f"vacs_connect_failed: {exc}",
            "akabak_pid": akabak_pid,
            "timeline": timeline,
        }
        log(
            "vacs_connect_failed",
            error=repr(exc),
            force_open_initial=force_open_initial,
            open_vacs_via_akabak=bool(args.open_vacs_via_akabak),
        )
        log_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return summary
    log("vacs_connected_or_started", pid=vacs_pid, started=vacs_started, exe=vacs_exe, open_meta=vacs_open_meta)

    baseline = _vacs_metrics(vacs_pid)
    log("vacs_baseline_metrics", metrics=baseline)
    baseline_fresh = not (
        int(baseline.get("max_controls_count", 0) or 0) >= 80 or int(baseline.get("max_graph_keyword_hits", 0) or 0) >= 5
    )
    if not baseline_fresh and bool(args.force_fresh_vacs):
        close_result = _close_vacs_without_saving(vacs_pid)
        log("vacs_force_fresh_close", result=close_result)
        _kill_vacs_processes()
        time.sleep(0.3)
        vacs_pid, vacs_started, vacs_open_meta = _connect_vacs_via_akabak(
            vacs_executable=vacs_exe,
            akabak_main=akabak_main,
            timeout_s=args.startup_timeout_s,
            force_open=True,
            require_akabak_launch=False,
            allow_existing_on_com_error=bool(args.allow_existing_vacs),
        )
        log("vacs_reopened_for_fresh_state", pid=vacs_pid, started=vacs_started, open_meta=vacs_open_meta)
        baseline = _vacs_metrics(vacs_pid)
        baseline_fresh = not (
            int(baseline.get("max_controls_count", 0) or 0) >= 80 or int(baseline.get("max_graph_keyword_hits", 0) or 0) >= 5
        )
        log("vacs_baseline_metrics_after_reopen", metrics=baseline, baseline_fresh=baseline_fresh)

    # Clear any stale AKABAK modal dialogs before triggering F7.
    for _ in range(3):
        stale_dialog = _find_any_akabak_dialog(akabak_pid, akabak_main=akabak_main)
        if stale_dialog is None:
            break
        action = _press_dialog_yes(stale_dialog["window"])
        log("akabak_stale_dialog_cleared", message=stale_dialog.get("message", ""), action=action)
        time.sleep(0.2)

    # Trigger transfer from AKABAK via F7.
    trigger_methods: List[str] = []
    main_hwnd = _window_handle(akabak_main)
    try:
        akabak_main.set_focus()
        akabak_main.type_keys("{F7}", set_foreground=True)
        trigger_methods.append("uia_type_keys_f7")
    except Exception as exc:
        log("f7_primary_error", error=repr(exc))
    if main_hwnd > 0:
        user32 = ctypes.windll.user32
        user32.PostMessageW(main_hwnd, WM_KEYDOWN, VK_F7, 0)
        user32.PostMessageW(main_hwnd, WM_KEYUP, VK_F7, 0)
        trigger_methods.append("hwnd_postmessage_f7")
    if not trigger_methods:
        summary = {
            "ok": False,
            "error": "Unable to trigger F7 on AKABAK main window.",
            "timeline": timeline,
        }
        log_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return summary
    log("f7_triggered", methods=trigger_methods)

    # Poll for edge-length confirm + graph re-import signal.
    started = time.perf_counter()
    last_change_at = started
    last_signature = (baseline.get("max_controls_count", 0), baseline.get("max_graph_keyword_hits", 0))
    edge_confirmations: List[Dict[str, Any]] = []
    poll_interval = 0.15
    f7_retry_done = False
    rpc_retry_count = 0

    while True:
        elapsed = time.perf_counter() - started
        if elapsed > float(args.timeout_s):
            summary = {
                "ok": False,
                "error": f"Timeout waiting for VACS graph re-import after {args.timeout_s}s.",
                "timeline": timeline,
                "edge_confirmations": edge_confirmations,
            }
            log_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return summary

        dialog = _find_edge_length_dialog(akabak_pid, akabak_main=akabak_main)
        if dialog is not None:
            confirm_result = _press_dialog_yes(dialog)
            confirm_result["dialog_message"] = _dialog_message(dialog)
            edge_confirmations.append(confirm_result)
            log("edge_length_dialog_handled", result=confirm_result)

        generic_dialog = _find_any_akabak_dialog(akabak_pid, akabak_main=akabak_main)
        if generic_dialog is not None:
            message_lower = str(generic_dialog.get("message", "")).lower()
            if "edge" not in message_lower or "length" not in message_lower:
                action = _press_dialog_yes(generic_dialog["window"])
                log("akabak_dialog_handled", message=generic_dialog.get("message", ""), action=action)
                if "rpc" in message_lower and "server" in message_lower:
                    rpc_retry_count += 1
                    if not bool(args.recover_rpc_by_restart):
                        summary = {
                            "ok": False,
                            "error": "AKABAK RPC server unavailable while triggering F7 transfer.",
                            "akabak_pid": akabak_pid,
                            "vacs_pid": vacs_pid,
                            "baseline_metrics": baseline,
                            "current_metrics": _vacs_metrics(vacs_pid),
                            "edge_confirmations": edge_confirmations,
                            "timeline": timeline,
                        }
                        log_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                        return summary
                    if rpc_retry_count > 2:
                        summary = {
                            "ok": False,
                            "error": "AKABAK RPC server unavailable while triggering F7 transfer.",
                            "akabak_pid": akabak_pid,
                            "vacs_pid": vacs_pid,
                            "baseline_metrics": baseline,
                            "current_metrics": _vacs_metrics(vacs_pid),
                            "edge_confirmations": edge_confirmations,
                            "timeline": timeline,
                        }
                        log_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                        return summary
                    try:
                        _kill_vacs_processes()
                        time.sleep(0.3)
                        vacs_pid, vacs_started, vacs_open_meta = _connect_vacs_via_akabak(
                            vacs_executable=vacs_exe,
                            akabak_main=akabak_main,
                            timeout_s=args.startup_timeout_s,
                            force_open=True,
                            require_akabak_launch=False,
                            allow_existing_on_com_error=bool(args.allow_existing_vacs),
                        )
                        log("vacs_restarted_after_rpc", pid=vacs_pid, started=vacs_started, open_meta=vacs_open_meta)
                    except Exception as exc:
                        log("vacs_restart_after_rpc_failed", error=repr(exc))
                    try:
                        akabak_main.set_focus()
                        akabak_main.type_keys("{F7}", set_foreground=True)
                    except Exception:
                        pass
                    if main_hwnd > 0:
                        user32 = ctypes.windll.user32
                        user32.PostMessageW(main_hwnd, WM_KEYDOWN, VK_F7, 0)
                        user32.PostMessageW(main_hwnd, WM_KEYUP, VK_F7, 0)
                    log("f7_retry_after_rpc", retry_count=rpc_retry_count)

        metrics = _vacs_metrics(vacs_pid)
        controls = int(metrics.get("max_controls_count", 0) or 0)
        hits = int(metrics.get("max_graph_keyword_hits", 0) or 0)
        graph_window_count = int(metrics.get("graph_window_count", 0) or 0)
        baseline_controls = int(baseline.get("max_controls_count", 0) or 0)
        baseline_hits = int(baseline.get("max_graph_keyword_hits", 0) or 0)
        controls_growth = controls - baseline_controls
        hits_growth = hits - baseline_hits
        signature = (controls, hits)
        if signature != last_signature:
            last_signature = signature
            last_change_at = time.perf_counter()
            log(
                "vacs_metrics_change",
                controls=controls,
                graph_keyword_hits=hits,
                graph_window_count=graph_window_count,
                controls_growth=controls_growth,
                graph_hits_growth=hits_growth,
            )

        graphs_imported = bool(
            graph_window_count > 0
            or
            controls >= 80
            or hits >= 5
            or (controls_growth >= 40 and hits_growth >= 2)
        )
        activity_seen = bool(graph_window_count > 0 or controls_growth > 0 or hits_growth > 0 or edge_confirmations or baseline_fresh)
        if graphs_imported and activity_seen:
            log("reimport_complete", metrics=metrics, controls_growth=controls_growth, graph_hits_growth=hits_growth)
            close_result = {}
            if bool(args.close_vacs_after):
                close_result = _close_vacs_without_saving(vacs_pid)
                log("vacs_close_after", result=close_result)
            summary = {
                "ok": True,
                "akabak_pid": akabak_pid,
                "vacs_pid": vacs_pid,
                "vacs_started": vacs_started,
                "trigger_methods": trigger_methods,
                "baseline_metrics": baseline,
                "final_metrics": metrics,
                "controls_growth": controls_growth,
                "graph_hits_growth": hits_growth,
                "edge_confirmations": edge_confirmations,
                "close_vacs_after": bool(args.close_vacs_after),
                "close_result": close_result,
                "timeline": timeline,
            }
            log_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return summary

        if (time.perf_counter() - last_change_at) >= float(args.idle_timeout_s):
            summary = {
                "ok": False,
                "error": f"No state change for {args.idle_timeout_s}s while waiting for re-import.",
                "akabak_pid": akabak_pid,
                "vacs_pid": vacs_pid,
                "baseline_metrics": baseline,
                "current_metrics": metrics,
                "edge_confirmations": edge_confirmations,
                "timeline": timeline,
            }
            log_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return summary

        if not f7_retry_done and elapsed >= 2.0 and controls_growth == 0 and hits_growth == 0 and not edge_confirmations:
            try:
                akabak_main.set_focus()
                akabak_main.type_keys("{F7}", set_foreground=True)
                if main_hwnd > 0:
                    user32 = ctypes.windll.user32
                    user32.PostMessageW(main_hwnd, WM_KEYDOWN, VK_F7, 0)
                    user32.PostMessageW(main_hwnd, WM_KEYUP, VK_F7, 0)
                f7_retry_done = True
                log("f7_retry_triggered", method="uia_type_keys_f7+hwnd_postmessage_f7")
            except Exception:
                if main_hwnd > 0:
                    user32 = ctypes.windll.user32
                    user32.PostMessageW(main_hwnd, WM_KEYDOWN, VK_F7, 0)
                    user32.PostMessageW(main_hwnd, WM_KEYUP, VK_F7, 0)
                    f7_retry_done = True
                    log("f7_retry_triggered", method="hwnd_postmessage_f7")

        time.sleep(poll_interval)
        poll_interval = min(0.35, poll_interval * 1.2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interim helper for AKABAK->VACS graph re-import via F7.")
    parser.add_argument("--akabak-exe", required=True, help="Path to AKABAK.exe (must already be running).")
    parser.add_argument("--vacs-exe", required=True, help="Path to VACS viewer executable.")
    parser.add_argument("--timeout-s", type=int, default=120, help="Global wait timeout for re-import.")
    parser.add_argument("--idle-timeout-s", type=int, default=10, help="Fail if no state change for this many seconds.")
    parser.add_argument("--startup-timeout-s", type=int, default=20, help="VACS start/connect timeout.")
    parser.add_argument(
        "--close-vacs-after",
        action="store_true",
        help="Close VACS after successful re-import (answers save prompt with No/Nein).",
    )
    parser.add_argument(
        "--force-fresh-vacs",
        action="store_true",
        default=False,
        help="If VACS already has imported graphs, close/reopen it first to get a fresh baseline.",
    )
    parser.set_defaults(allow_existing_vacs=True)
    parser.add_argument(
        "--allow-existing-vacs",
        action="store_true",
        help=(
            "Allow attaching an already running VACS instance if AKABAK menu open path returns COM error."
        ),
    )
    parser.add_argument(
        "--disallow-existing-vacs",
        action="store_false",
        dest="allow_existing_vacs",
        help="Disallow fallback attach to existing VACS instances.",
    )
    parser.set_defaults(open_vacs_via_akabak=True)
    parser.add_argument(
        "--open-vacs-via-akabak",
        action="store_true",
        help="Open/activate VACS via AKABAK menu (default).",
    )
    parser.add_argument(
        "--skip-open-vacs-via-akabak",
        action="store_false",
        dest="open_vacs_via_akabak",
        help="Skip AKABAK menu and only attach to an existing VACS instance.",
    )
    parser.add_argument(
        "--recover-rpc-by-restart",
        action="store_true",
        help="If RPC error appears after F7, attempt legacy restart/retry flow.",
    )
    parser.add_argument(
        "--output-dir",
        default="runner_test_workspace/logs/interim_reimport",
        help="Directory for run JSON output.",
    )
    return parser


def main() -> int:
    from app.audit_mode import enable_audit_mode

    enable_audit_mode(entrypoint="scripts.vacs_interim_reimport.main")
    parser = build_parser()
    args = parser.parse_args()
    result = run_interim(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
