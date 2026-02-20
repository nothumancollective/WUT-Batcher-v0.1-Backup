"""Save all VACS graph child windows to TXT files in ATH version folder.

Non-visual automation only:
- process-bound UIA/Win32 controls
- no pixel/OCR-based branching
"""

from __future__ import annotations

import argparse
import csv
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from pywinauto import Desktop


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


WM_COMMAND = 0x0111
WM_SETTEXT = 0x000C
WM_CLOSE = 0x0010
WM_MDIDESTROY = 0x0221
WM_MDIACTIVATE = 0x0222
BM_CLICK = 0x00F5
BN_CLICKED = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_F7 = 0x76
VACS_EXPORT_COMMAND_ID = 52
GWL_STYLE = -16
BS_DEFPUSHBUTTON = 0x00000001
IDOK = 1
IDCANCEL = 2
IDYES = 6
IDNO = 7
IDCLOSE = 8

GRAPH_CLASSES = {"TForm_DatGraph", "TForm_DatContour"}
CHILD_CLASSES = {"TForm_DatGraph", "TForm_DatContour", "TForm_Editor"}
FAST_PRE_EXPORT_GRAPH_READY_TIMEOUT_S = 0.5
FAST_PRE_EXPORT_GRAPH_POLL_S = 0.02
FAST_ASSUME_READY_QUICK_TIMEOUT_S = 0.7
FAST_ASSUME_READY_TIMEOUT_S = 3.0
FAST_GRAPH_STABILIZE_QUICK_TIMEOUT_S = 0.35
FAST_GRAPH_STABLE_FOR_QUICK_S = 0.08
FAST_GRAPH_STABILIZE_TIMEOUT_S = 1.6
FAST_GRAPH_STABLE_FOR_S = 0.25
FAST_GRAPH_STABILIZE_POLL_S = 0.03


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sig(ctrl: Any) -> Dict[str, Any]:
    info = getattr(ctrl, "element_info", None)
    def _safe_attr(name: str, default: Any = "") -> Any:
        try:
            return getattr(info, name, default)
        except Exception:
            return default
    return {
        "handle": int(_safe_attr("handle", 0) or 0),
        "title": str(_safe_attr("name", "") or ""),
        "class_name": str(_safe_attr("class_name", "") or ""),
        "control_type": str(_safe_attr("control_type", "") or ""),
        "automation_id": str(_safe_attr("automation_id", "") or ""),
        "process_id": int(_safe_attr("process_id", 0) or 0),
    }


def _window_text(ctrl: Any) -> str:
    try:
        return str(ctrl.window_text() or "").strip()
    except Exception:
        try:
            return str(getattr(ctrl.element_info, "name", "") or "").strip()
        except Exception:
            return ""


def _windows_for_pid(pid: int) -> List[Any]:
    try:
        return list(Desktop(backend="uia").windows(process=int(pid)))
    except Exception:
        return []


def _top_windows_for_pid_fast(pid: int) -> List[Any]:
    """Fast top-level window scan for hot paths."""
    try:
        return list(Desktop(backend="win32").windows(process=int(pid)))
    except Exception:
        try:
            return list(Desktop(backend="uia").windows(process=int(pid)))
        except Exception:
            return []


def _find_main(pid: int) -> Optional[Any]:
    for w in _windows_for_pid(pid):
        if _sig(w).get("class_name") == "TForm_DatMain":
            return w
    return None


def _find_main_fast(pid: int) -> Optional[Any]:
    for w in _top_windows_for_pid_fast(int(pid)):
        if str(_sig(w).get("class_name", "") or "") == "TForm_DatMain":
            return w
    return _find_main(int(pid))


def _graph_children(main: Any) -> List[Any]:
    rows: Dict[int, Any] = {}
    for c in main.descendants():
        s = _sig(c)
        if s.get("control_type") != "Window":
            continue
        class_name = str(s.get("class_name", "") or "")
        title = str(s.get("title", "") or "")
        if class_name not in GRAPH_CLASSES:
            continue
        # Hard guard: editor helper panes are not graph windows.
        if re.match(r"^\s*Editor\s+\d+\s*$", title, re.IGNORECASE):
            continue
        h = int(s.get("handle", 0) or 0)
        if h > 0:
            rows[h] = c
    return list(rows.values())


def _graph_children_fast(pid: int, *, main_hint: Optional[Any] = None) -> List[Any]:
    rows: Dict[int, Any] = {}
    main_handle = int(_sig(main_hint).get("handle", 0) or 0) if main_hint is not None else 0
    for w in _top_windows_for_pid_fast(int(pid)):
        s = _sig(w)
        class_name = str(s.get("class_name", "") or "")
        if class_name not in GRAPH_CLASSES:
            continue
        h = int(s.get("handle", 0) or 0)
        if h <= 0 or (main_handle > 0 and h == main_handle):
            continue
        title = str(s.get("title", "") or "")
        if re.match(r"^\s*Editor\s+\d+\s*$", title, re.IGNORECASE):
            continue
        rows[h] = w
    if rows:
        return list(rows.values())

    # Fallback to UIA main window for reliable descendant enumeration.
    uia_main = _find_main(int(pid))
    if uia_main is not None:
        return _graph_children(uia_main)
    if main_hint is not None:
        return _graph_children(main_hint)
    main = _find_main_fast(int(pid))
    if main is None:
        return []
    return _graph_children(main)


def _wait_initial_graphs_fast(vacs_pid: int, *, timeout_s: float) -> Dict[str, Any]:
    deadline = time.perf_counter() + max(0.05, float(timeout_s))
    main = _find_main_fast(int(vacs_pid))
    while time.perf_counter() < deadline:
        main = main or _find_main_fast(int(vacs_pid))
        if main is None:
            time.sleep(FAST_PRE_EXPORT_GRAPH_POLL_S)
            continue
        graphs = _graph_children_fast(int(vacs_pid), main_hint=main)
        if graphs:
            return {"main": main, "graphs": graphs}
        time.sleep(FAST_PRE_EXPORT_GRAPH_POLL_S)
    return {"main": main, "graphs": []}


def _collect_graphs_until_stable_fast(
    vacs_pid: int,
    *,
    main_hint: Optional[Any],
    initial_graphs: Optional[List[Any]],
    timeout_s: float,
    stable_for_s: float,
) -> Dict[str, Any]:
    deadline = time.perf_counter() + max(0.1, float(timeout_s))
    stable_for = max(0.05, float(stable_for_s))
    main = main_hint
    rows: Dict[int, Any] = {}
    observed_counts: List[int] = []
    for g in list(initial_graphs or []):
        h = int(_sig(g).get("handle", 0) or 0)
        if h > 0:
            rows[h] = g
    last_growth_at = time.perf_counter()
    while time.perf_counter() < deadline:
        main = main or _find_main_fast(int(vacs_pid))
        if main is None:
            time.sleep(FAST_GRAPH_STABILIZE_POLL_S)
            continue
        current = _graph_children_fast(int(vacs_pid), main_hint=main)
        seen_before = len(rows)
        for g in current:
            h = int(_sig(g).get("handle", 0) or 0)
            if h > 0:
                rows[h] = g
        observed_counts.append(int(len(rows)))
        if len(rows) > seen_before:
            last_growth_at = time.perf_counter()
        elif rows and (time.perf_counter() - last_growth_at) >= stable_for:
            break
        time.sleep(FAST_GRAPH_STABILIZE_POLL_S)
    return {
        "main": main,
        "graphs": list(rows.values()),
        "observed_counts": observed_counts[-20:],
        "stable_elapsed_s": max(0.0, time.perf_counter() - last_growth_at),
    }


def _dialog_candidates(pid: int, main_handle: int) -> List[Any]:
    rows: List[Any] = []
    for w in _windows_for_pid(pid):
        s = _sig(w)
        h = int(s.get("handle", 0) or 0)
        if h <= 0 or h == int(main_handle):
            continue
        rows.append(w)
    main = _find_main(pid)
    if main is not None:
        for c in main.descendants():
            s = _sig(c)
            if s.get("control_type") != "Window":
                continue
            h = int(s.get("handle", 0) or 0)
            if h <= 0 or h == int(main_handle):
                continue
            if s.get("class_name") in CHILD_CLASSES:
                continue
            rows.append(c)
    uniq: Dict[int, Any] = {}
    for w in rows:
        h = int(_sig(w).get("handle", 0) or 0)
        if h > 0:
            uniq[h] = w
    return list(uniq.values())


def _kill_vacs() -> None:
    for image in ("VACSVIEWER_32.exe", "vacsviewer.exe"):
        subprocess.run(["taskkill", "/IM", image, "/T", "/F"], capture_output=True, text=True, check=False)


def _list_pids_by_image(image_name: str) -> List[int]:
    image = str(image_name or "").strip()
    if not image:
        return []
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception:
        return []
    rows: List[int] = []
    for row in csv.reader((proc.stdout or "").splitlines()):
        if len(row) < 2:
            continue
        image_cell = str(row[0] or "").strip().strip('"').lower()
        pid_cell = str(row[1] or "").strip().strip('"')
        if image_cell != image.lower():
            continue
        if not pid_cell.isdigit():
            continue
        rows.append(int(pid_cell))
    return sorted(set(rows))


def _running_vacs_pids() -> List[int]:
    rows: List[int] = []
    rows.extend(_list_pids_by_image("VACSVIEWER_32.exe"))
    rows.extend(_list_pids_by_image("vacsviewer.exe"))
    return sorted(set(rows))


def _select_ready_vacs_pid(*, timeout_s: float = 0.0, poll_s: float = 0.05) -> Dict[str, Any]:
    deadline = time.perf_counter() + max(0.0, float(timeout_s))
    last_candidates: List[Dict[str, Any]] = []
    while True:
        candidates: List[Dict[str, Any]] = []
        for pid in _running_vacs_pids():
            main = _find_main_fast(int(pid))
            if main is None:
                candidates.append({"pid": int(pid), "main_present": False, "graph_count": 0})
                continue
            graphs = _graph_children_fast(int(pid), main_hint=main)
            candidates.append(
                {
                    "pid": int(pid),
                    "main_present": True,
                    "graph_count": int(len(graphs)),
                    "graph_titles_preview": [str(_sig(g).get("title", "") or "") for g in list(graphs)[:6]],
                    "main_signature": _sig(main),
                }
            )
        ready = [row for row in candidates if bool(row.get("main_present")) and int(row.get("graph_count", 0)) > 0]
        selected = sorted(ready, key=lambda row: int(row.get("graph_count", 0)), reverse=True)[:1]
        if selected:
            return {
                "candidates": candidates,
                "selected": selected[0],
            }
        last_candidates = candidates
        if time.perf_counter() >= deadline:
            return {
                "candidates": last_candidates,
                "selected": None,
            }
        time.sleep(max(0.01, float(poll_s)))


def _run_interim(args: argparse.Namespace) -> Dict[str, Any]:
    return _run_interim_with_mode(args, skip_open_via_akabak=False)


def _run_interim_with_mode(args: argparse.Namespace, *, skip_open_via_akabak: bool) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "vacs_interim_reimport.py"),
        "--akabak-exe",
        str(args.akabak_exe),
        "--vacs-exe",
        str(args.vacs_exe),
        "--allow-existing-vacs",
        "--idle-timeout-s",
        str(args.interim_idle_timeout_s),
        "--timeout-s",
        str(args.interim_timeout_s),
        "--startup-timeout-s",
        str(args.interim_startup_timeout_s),
    ]
    if skip_open_via_akabak:
        cmd.append("--skip-open-vacs-via-akabak")
    if bool(getattr(args, "interim_recover_rpc", False)):
        cmd.append("--recover-rpc-by-restart")
    cp = subprocess.run(cmd, capture_output=True, text=True, check=False)
    out = str(cp.stdout or "").strip()
    payload: Dict[str, Any] = {"returncode": int(cp.returncode), "stderr": str(cp.stderr or "").strip(), "stdout_tail": out[-4000:]}
    try:
        payload["parsed"] = json.loads(out) if out else {}
    except Exception:
        payload["parsed"] = {}
    return payload


def _find_data_export(pid: int, main_handle: int, timeout_s: float) -> Optional[Any]:
    deadline = time.perf_counter() + float(timeout_s)
    while time.perf_counter() < deadline:
        for d in _dialog_candidates(pid, main_handle):
            s = _sig(d)
            title = str(s.get("title", ""))
            class_name = str(s.get("class_name", ""))
            if re.search(r"data\s*export", title, re.IGNORECASE):
                return d
            if class_name == "TForm_Export":
                return d
        time.sleep(0.03)
    return None


def _find_data_export_after_trigger(
    pid: int,
    main_handle: int,
    timeout_s: float,
    known_handles: set[int],
) -> Optional[Any]:
    deadline = time.perf_counter() + float(timeout_s)
    fallback: Optional[Any] = None
    while time.perf_counter() < deadline:
        dialogs = _dialog_candidates(pid, int(main_handle))
        exports: List[Any] = []
        for d in dialogs:
            s = _sig(d)
            if str(s.get("class_name", "")) != "TForm_Export":
                continue
            exports.append(d)
        if exports:
            for d in exports:
                h = int(_sig(d).get("handle", 0) or 0)
                if h > 0 and h not in known_handles:
                    return d
            fallback = exports[0]
        time.sleep(0.02)
    return fallback


def _find_data_export_after_trigger_fast(pid: int, timeout_s: float, known_handles: set[int]) -> Optional[Any]:
    deadline = time.perf_counter() + float(timeout_s)
    fallback: Optional[Any] = None
    while time.perf_counter() < deadline:
        for d in _top_windows_for_pid_fast(int(pid)):
            s = _sig(d)
            if str(s.get("class_name", "")) != "TForm_Export":
                continue
            h = int(s.get("handle", 0) or 0)
            if h > 0 and h not in known_handles:
                return d
            fallback = d
        time.sleep(0.01)
    return fallback


def _dialog_controls(dialog: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for c in list(dialog.descendants())[:220]:
        s = _sig(c)
        s["text"] = _window_text(c)
        rows.append(s)
    return rows


def _win32_children(hwnd: int) -> List[Dict[str, Any]]:
    if int(hwnd or 0) <= 0:
        return []
    user32 = ctypes.windll.user32
    get_class = user32.GetClassNameW
    get_text = user32.GetWindowTextW
    get_id = user32.GetDlgCtrlID
    get_style = user32.GetWindowLongW
    get_rect = user32.GetWindowRect
    rows: List[Dict[str, Any]] = []
    enum_child_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cls(h: int) -> str:
        b = ctypes.create_unicode_buffer(256)
        get_class(int(h), b, 255)
        return str(b.value or "")

    def txt(h: int) -> str:
        b = ctypes.create_unicode_buffer(512)
        get_text(int(h), b, 511)
        return str(b.value or "")

    def cb(chwnd, _lparam):
        h = int(chwnd)
        rect = ctypes.wintypes.RECT()
        has_rect = bool(get_rect(int(h), ctypes.byref(rect)))
        rows.append(
            {
                "handle": h,
                "class_name": cls(h),
                "text": txt(h),
                "ctrl_id": int(get_id(h)),
                "style": int(get_style(int(h), int(GWL_STYLE))),
                "rect": {
                    "left": int(rect.left) if has_rect else 0,
                    "top": int(rect.top) if has_rect else 0,
                    "right": int(rect.right) if has_rect else 0,
                    "bottom": int(rect.bottom) if has_rect else 0,
                },
            }
        )
        return True

    user32.EnumChildWindows(int(hwnd), enum_child_proc(cb), 0)
    return rows


def _is_default_pushbutton(style: int) -> bool:
    try:
        return bool(int(style) & int(BS_DEFPUSHBUTTON))
    except Exception:
        return False


def _sorted_button_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buttons: List[Dict[str, Any]] = []
    for r in rows:
        cls = str(r.get("class_name", "") or "")
        if cls.lower() == "trzdialogbuttons":
            continue
        if not re.search(r"(button|bitbtn)", cls, re.IGNORECASE):
            continue
        buttons.append(r)

    def _key(row: Dict[str, Any]) -> tuple:
        rect = row.get("rect") or {}
        return (
            0 if _is_default_pushbutton(int(row.get("style", 0) or 0)) else 1,
            int(rect.get("top", 0) or 0),
            int(rect.get("left", 0) or 0),
        )

    return sorted(buttons, key=_key)


def _bitbtn_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in _sorted_button_rows(rows):
        cls = str(r.get("class_name", "") or "")
        if re.search(r"(rzbitbtn)", cls, re.IGNORECASE):
            out.append(r)
    return out


def _press_dialog_button_handle(dialog_handle: int, button_row: Dict[str, Any]) -> Dict[str, Any]:
    user32 = ctypes.windll.user32
    bh = int(button_row.get("handle", 0) or 0)
    ctrl_id = int(button_row.get("ctrl_id", 0) or 0)
    if bh <= 0:
        return {"status": "error", "error": "invalid_button_handle"}
    attempts: List[Dict[str, Any]] = []
    try:
        user32.SendMessageW(int(bh), BM_CLICK, 0, 0)
        attempts.append({"method": "bm_click", "status": "ok", "button_handle": bh, "ctrl_id": ctrl_id})
    except Exception as exc:
        attempts.append({"method": "bm_click", "status": "error", "error": repr(exc), "button_handle": bh, "ctrl_id": ctrl_id})
    if int(dialog_handle) > 0 and ctrl_id > 0:
        try:
            wparam = (int(ctrl_id) & 0xFFFF) | ((int(BN_CLICKED) & 0xFFFF) << 16)
            user32.SendMessageW(int(dialog_handle), WM_COMMAND, int(wparam), int(bh))
            attempts.append({"method": "wm_command_bn_clicked", "status": "ok", "button_handle": bh, "ctrl_id": ctrl_id})
        except Exception as exc:
            attempts.append({"method": "wm_command_bn_clicked", "status": "error", "error": repr(exc), "button_handle": bh, "ctrl_id": ctrl_id})
    return {"status": "ok", "attempts": attempts, "button": button_row}


def _find_mdi_client_handle(main_handle: int) -> int:
    for row in _win32_children(int(main_handle)):
        if str(row.get("class_name", "") or "").lower() == "mdiclient":
            return int(row.get("handle", 0) or 0)
    return 0


def _click_handle(hwnd: int) -> bool:
    if int(hwnd or 0) <= 0:
        return False
    try:
        ctypes.windll.user32.SendMessageW(int(hwnd), BM_CLICK, 0, 0)
        return True
    except Exception:
        return False


def _send_f7(hwnd: int) -> bool:
    if int(hwnd or 0) <= 0:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.PostMessageW(int(hwnd), WM_KEYDOWN, VK_F7, 0)
        user32.PostMessageW(int(hwnd), WM_KEYUP, VK_F7, 0)
        return True
    except Exception:
        return False


def _dialog_message_text(dialog: Any) -> str:
    parts: List[str] = []
    try:
        for c in list(dialog.descendants())[:80]:
            txt = _window_text(c)
            if txt:
                parts.append(txt)
    except Exception:
        pass
    if parts:
        return " | ".join(parts[:12])
    return _window_text(dialog)


def _dialog_button_labels(dialog: Any) -> List[str]:
    labels: List[str] = []
    try:
        for c in dialog.descendants():
            ct = str(_sig(c).get("control_type", "") or "")
            if ct != "Button":
                continue
            txt = _window_text(c)
            if txt:
                labels.append(txt)
    except Exception:
        pass
    h = int(_sig(dialog).get("handle", 0) or 0)
    if h > 0:
        for row in _win32_children(h):
            cls = str(row.get("class_name", "") or "")
            if not re.search(r"(button|bitbtn)", cls, re.IGNORECASE):
                continue
            txt = str(row.get("text", "") or "")
            if txt:
                labels.append(txt)
    # preserve order while deduplicating
    seen: Dict[str, bool] = {}
    out: List[str] = []
    for x in labels:
        if x in seen:
            continue
        seen[x] = True
        out.append(x)
    return out


def _close_dialog(dialog: Any) -> Dict[str, Any]:
    title = _window_text(dialog)
    message = _dialog_message_text(dialog).lower()
    is_confirm = bool(re.search(r"(confirm|best.tigen|please confirm|warn|warning)", f"{title} {message}", re.IGNORECASE))

    preferred: List[str]
    if is_confirm and re.search(r"(save|speicher|overwrite|replace|ersetzen)", message, re.IGNORECASE):
        preferred = ["OK", "Ok", "Yes", "Ja", "No", "Nein", "Dont Save", "Nicht speichern"]
    elif is_confirm:
        preferred = ["OK", "Ok", "Schließen", "Close", "Yes", "Ja", "No", "Nein", "Cancel", "Abbrechen"]
    else:
        preferred = ["Close", "Schließen", "Cancel", "Abbrechen", "No", "Nein", "OK", "Ok", "Yes", "Ja"]

    for caption in preferred:
        try:
            btn = dialog.child_window(title=caption)
            if btn.exists(timeout=0.2):
                try:
                    btn.invoke()
                    return {"status": "ok", "method": "invoke", "caption": caption, "dialog_title": title}
                except Exception:
                    try:
                        btn.click()
                        return {"status": "ok", "method": "click", "caption": caption, "dialog_title": title}
                    except Exception:
                        pass
        except Exception:
            continue

    try:
        descendants = list(dialog.descendants())
    except Exception:
        descendants = []
    for caption in preferred:
        wanted = caption.strip().lower()
        if not wanted:
            continue
        for c in descendants:
            label = _window_text(c).strip().lower()
            if not label or label != wanted:
                continue
            try:
                c.invoke()
                return {"status": "ok", "method": "invoke_descendant", "caption": label, "dialog_title": title}
            except Exception:
                try:
                    c.click()
                    return {"status": "ok", "method": "click_descendant", "caption": label, "dialog_title": title}
                except Exception:
                    h = int(_sig(c).get("handle", 0) or 0)
                    if h > 0 and _click_handle(h):
                        return {"status": "ok", "method": "bm_click_descendant", "caption": label, "dialog_title": title}
    try:
        dialog.type_keys("{ESC}")
        return {"status": "ok", "method": "esc", "dialog_title": title}
    except Exception as exc:
        return {"status": "error", "error": repr(exc), "dialog_title": title}


def _click_caption(dialog: Any, caption: str) -> Dict[str, Any]:
    want = str(caption or "").strip().lower()
    if not want:
        return {"status": "error", "error": "empty_caption"}
    try:
        btn = dialog.child_window(title=caption)
        if btn.exists(timeout=0.1):
            try:
                btn.invoke()
                return {"status": "ok", "method": "invoke", "caption": caption}
            except Exception:
                try:
                    btn.click()
                    return {"status": "ok", "method": "click", "caption": caption}
                except Exception:
                    pass
    except Exception:
        pass
    try:
        for c in dialog.descendants():
            label = _window_text(c).strip().lower()
            if label != want:
                continue
            try:
                c.invoke()
                return {"status": "ok", "method": "invoke_descendant", "caption": label}
            except Exception:
                try:
                    c.click()
                    return {"status": "ok", "method": "click_descendant", "caption": label}
                except Exception:
                    h = int(_sig(c).get("handle", 0) or 0)
                    if h > 0 and _click_handle(h):
                        return {"status": "ok", "method": "bm_click_descendant", "caption": label}
    except Exception:
        pass
    return {"status": "error", "error": f"caption_not_found:{caption}"}


def _handle_close_confirm_dialog(dialog: Any, vacs_pid: int, child_handle: int) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    for caption in ("OK", "Ok", "Schließen", "Close", "Cancel", "Abbrechen"):
        action = _click_caption(dialog, caption)
        attempts.append({"caption": caption, "action": action})
        time.sleep(0.12)
        if not _is_graph_child_open(int(vacs_pid), int(child_handle)):
            return {"status": "ok", "resolved": True, "attempts": attempts}
    return {"status": "ok", "resolved": False, "attempts": attempts}


def _resolve_confirm_dialog(dialog: Any) -> Dict[str, Any]:
    dialog_handle = int(_sig(dialog).get("handle", 0) or 0)
    title = str(_sig(dialog).get("title", "") or "")
    msg = _dialog_message_text(dialog)
    labels = [x.strip().lower() for x in _dialog_button_labels(dialog)]
    attempts: List[Dict[str, Any]] = []

    # Primary: locale-agnostic Win32 button handling.
    if dialog_handle > 0:
        rows = _bitbtn_rows(_win32_children(dialog_handle))
        if not rows:
            rows = _sorted_button_rows(_win32_children(dialog_handle))
        by_id: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            cid = int(r.get("ctrl_id", 0) or 0)
            if cid > 0 and cid not in by_id:
                by_id[cid] = r
        ordered: List[Dict[str, Any]] = []
        for cid in (IDCLOSE, IDOK, IDYES, IDNO, IDCANCEL):
            if cid in by_id:
                ordered.append(by_id[cid])
        for r in rows:
            if r not in ordered:
                ordered.append(r)
        for r in ordered[:6]:
            action = _press_dialog_button_handle(dialog_handle, r)
            attempts.append({"method": "win32_handle", "action": action})
            time.sleep(0.06)
            if not _is_window_alive(dialog_handle):
                return {"status": "ok", "closed": True, "attempts": attempts}
            try:
                ctypes.windll.user32.PostMessageW(int(dialog_handle), WM_CLOSE, 0, 0)
                attempts.append({"method": "win32_handle_post_wm_close", "status": "ok"})
                time.sleep(0.05)
            except Exception as exc:
                attempts.append({"method": "win32_handle_post_wm_close", "status": "error", "error": repr(exc)})
            if not _is_window_alive(dialog_handle):
                return {"status": "ok", "closed": True, "attempts": attempts}

    if "schließen" in labels:
        preferred = ("Schließen", "Close", "OK", "Ok", "Yes", "Ja")
    elif re.search(r"(overwrite|replace|ersetzen|save|speicher)", f"{title} {msg}", re.IGNORECASE):
        preferred = ("Yes", "Ja", "OK", "Ok", "Replace", "Ersetzen")
    else:
        preferred = ("OK", "Ok", "Yes", "Ja", "Continue", "Fortfahren", "Close", "Schließen")

    for caption in preferred:
        action = _click_caption(dialog, caption)
        attempts.append({"caption": caption, "action": action})
        if str(action.get("status", "")) == "ok":
            time.sleep(0.08)
            if dialog_handle <= 0 or not _is_window_alive(dialog_handle):
                return {"status": "ok", "closed": True, "attempts": attempts}
            return {"status": "partial", "closed": False, "attempts": attempts}
    try:
        dialog.type_keys("{ENTER}")
        attempts.append({"caption": "{ENTER}", "action": {"status": "ok", "method": "enter"}})
        time.sleep(0.08)
        closed = dialog_handle <= 0 or not _is_window_alive(dialog_handle)
        return {"status": "ok" if closed else "partial", "closed": closed, "attempts": attempts}
    except Exception as exc:
        attempts.append({"caption": "{ENTER}", "action": {"status": "error", "error": repr(exc)}})
        return {"status": "error", "closed": False, "attempts": attempts}


def _is_window_alive(hwnd: int) -> bool:
    if int(hwnd or 0) <= 0:
        return False
    try:
        return bool(ctypes.windll.user32.IsWindow(int(hwnd)))
    except Exception:
        return False


def _drain_aux_dialogs(vacs_pid: int, main_handle: int, keep_export_handle: int = 0) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for d in _dialog_candidates(int(vacs_pid), int(main_handle)):
        s = _sig(d)
        cls = str(s.get("class_name", ""))
        if cls == "TForm_Confirm":
            rows.append({"dialog": s, "action": _resolve_confirm_dialog(d)})
    return rows


def _drain_aux_dialogs_fast(vacs_pid: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for d in _top_windows_for_pid_fast(int(vacs_pid)):
        s = _sig(d)
        cls = str(s.get("class_name", ""))
        if cls not in {"TForm_Confirm", "#32770"}:
            continue
        try:
            action = _resolve_confirm_dialog(d)
            rows.append({"dialog": s, "action": action})
        except Exception as exc:
            rows.append({"dialog": s, "action": {"status": "error", "error": repr(exc)}})
    return rows


def _close_all_export_dialogs(vacs_pid: int, main_handle: int, timeout_s: float = 3.0) -> List[Dict[str, Any]]:
    def _force_close_export_dialog(dialog: Any) -> Dict[str, Any]:
        sig = _sig(dialog)
        h = int(sig.get("handle", 0) or 0)
        attempts: List[Dict[str, Any]] = []
        if h > 0:
            try:
                ctypes.windll.user32.PostMessageW(int(h), WM_CLOSE, 0, 0)
                attempts.append({"method": "wm_close_post", "status": "ok", "handle": h})
            except Exception as exc:
                attempts.append({"method": "wm_close_post", "status": "error", "error": repr(exc), "handle": h})
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 0.35:
                if not _is_window_alive(h):
                    return {"status": "ok", "closed": True, "attempts": attempts}
                time.sleep(0.05)
        action = _close_dialog(dialog)
        attempts.append({"method": "close_dialog", "action": action})
        if h > 0:
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 0.35:
                if not _is_window_alive(h):
                    return {"status": "ok", "closed": True, "attempts": attempts}
                time.sleep(0.05)
            return {"status": "ok", "closed": False, "attempts": attempts}
        return {"status": "ok", "closed": None, "attempts": attempts}

    rows: List[Dict[str, Any]] = []
    deadline = time.perf_counter() + float(timeout_s)
    while time.perf_counter() < deadline:
        did_any = False
        for d in _dialog_candidates(int(vacs_pid), int(main_handle)):
            s = _sig(d)
            cls = str(s.get("class_name", ""))
            if cls == "TForm_Confirm":
                rows.append({"dialog": s, "action": _resolve_confirm_dialog(d)})
                did_any = True
                continue
            if cls == "TForm_Export":
                rows.append({"dialog": s, "action": _force_close_export_dialog(d)})
                did_any = True
                continue
        if not did_any:
            break
        time.sleep(0.08)
    return rows


def _wait_for_dialog_quiescence(vacs_pid: int, main_handle: int, timeout_s: float = 5.0, stable_s: float = 0.25) -> Dict[str, Any]:
    """Drain confirm/export dialogs until none remain for a stable interval."""
    actions: List[Dict[str, Any]] = []
    remaining: List[Dict[str, Any]] = []
    stable_since: Optional[float] = None
    deadline = time.perf_counter() + float(timeout_s)
    while time.perf_counter() < deadline:
        dialogs = _dialog_candidates(int(vacs_pid), int(main_handle))
        actionable = []
        for d in dialogs:
            s = _sig(d)
            cls = str(s.get("class_name", "") or "")
            if cls in CHILD_CLASSES:
                continue
            if cls in {"TForm_Confirm", "TForm_Export"}:
                actionable.append(d)
                continue
            title = str(s.get("title", "") or "")
            if re.search(r"(confirm|warning|warn|please confirm|best.tigen|save|overwrite)", title, re.IGNORECASE):
                actionable.append(d)

        if not actionable:
            if stable_since is None:
                stable_since = time.perf_counter()
            if (time.perf_counter() - stable_since) >= float(stable_s):
                return {"status": "ok", "quiescent": True, "actions": actions, "remaining": []}
            time.sleep(0.08)
            continue

        stable_since = None
        for d in actionable:
            s = _sig(d)
            cls = str(s.get("class_name", "") or "")
            if cls == "TForm_Confirm":
                action = _resolve_confirm_dialog(d)
            else:
                action = _close_dialog(d)
            actions.append(
                {
                    "dialog": s,
                    "dialog_message": _dialog_message_text(d),
                    "dialog_buttons": _dialog_button_labels(d),
                    "action": action,
                }
            )
        time.sleep(0.08)

    for d in _dialog_candidates(int(vacs_pid), int(main_handle)):
        s = _sig(d)
        if str(s.get("class_name", "") or "") in CHILD_CLASSES:
            continue
        remaining.append(
            {
                "signature": s,
                "message": _dialog_message_text(d),
                "buttons": _dialog_button_labels(d),
            }
        )
    return {"status": "timeout", "quiescent": False, "actions": actions, "remaining": remaining}


def _find_save_as_dialog(target_pid: int, main_handle: int, timeout_s: float) -> Optional[Any]:
    def _has_filename_edit(dialog: Any) -> bool:
        hwnd = int(_sig(dialog).get("handle", 0) or 0)
        if hwnd <= 0:
            return False
        rows = _win32_children(hwnd)
        if any(int(r.get("ctrl_id", -1)) == 1148 for r in rows):
            return True
        edits = [r for r in rows if str(r.get("class_name", "")).lower() == "edit"]
        return bool(edits)

    def _resolve_intermediate_confirm(dialog: Any) -> None:
        _resolve_confirm_dialog(dialog)

    deadline = time.perf_counter() + float(timeout_s)
    while time.perf_counter() < deadline:
        for w in _windows_for_pid(int(target_pid)):
            s = _sig(w)
            class_name = str(s.get("class_name", ""))
            handle = int(s.get("handle", 0) or 0)
            if handle <= 0 or handle == int(main_handle):
                continue
            if class_name == "TForm_Confirm":
                _resolve_intermediate_confirm(w)
                continue
            if re.search(r"(#32770|TForm_.*)", class_name, re.IGNORECASE):
                if _has_filename_edit(w):
                    return w
        # Fallback on process-scoped descendant dialogs under main.
        main = _find_main(int(target_pid))
        if main is not None:
            for d in _dialog_candidates(int(target_pid), int(main_handle)):
                s = _sig(d)
                cls = str(s.get("class_name", ""))
                if cls == "TForm_Confirm":
                    _resolve_intermediate_confirm(d)
                    continue
                if cls in {"TForm_Export", *CHILD_CLASSES}:
                    continue
                if _has_filename_edit(d):
                    return d
        time.sleep(0.03)
    return None


def _find_save_as_dialog_fast(target_pid: int, timeout_s: float) -> Optional[Any]:
    def _has_filename_edit(dialog: Any) -> bool:
        hwnd = int(_sig(dialog).get("handle", 0) or 0)
        if hwnd <= 0:
            return False
        rows = _win32_children(hwnd)
        if any(int(r.get("ctrl_id", -1)) == 1148 for r in rows):
            return True
        return bool([r for r in rows if str(r.get("class_name", "")).lower() == "edit"])

    deadline = time.perf_counter() + float(timeout_s)
    while time.perf_counter() < deadline:
        for w in _top_windows_for_pid_fast(int(target_pid)):
            s = _sig(w)
            class_name = str(s.get("class_name", ""))
            if class_name == "TForm_Confirm":
                _resolve_confirm_dialog(w)
                continue
            if not re.search(r"(#32770|TForm_.*)", class_name, re.IGNORECASE):
                continue
            if class_name == "TForm_Export":
                continue
            if _has_filename_edit(w):
                return w
        time.sleep(0.01)
    return None


def _set_save_path(dialog: Any, full_target_path: Path, *, quick: bool = False) -> Dict[str, Any]:
    target = str(full_target_path)
    user32 = ctypes.windll.user32
    result: Dict[str, Any] = {"target": target}

    # Prefer file-name edit by id 1148 in common file dialog.
    rows = _win32_children(int(_sig(dialog).get("handle", 0) or 0))
    filename_row = None
    for r in rows:
        if int(r.get("ctrl_id", -1)) == 1148:
            filename_row = r
            break
    if filename_row is None:
        # fallback: first Edit control
        for r in rows:
            if str(r.get("class_name", "")) == "Edit":
                filename_row = r
                break

    if filename_row is not None:
        h = int(filename_row.get("handle", 0) or 0)
        user32.SendMessageW(h, WM_SETTEXT, 0, target)
        buf = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(h, buf, 1023)
        readback = str(buf.value or "")
        result["filename_handle"] = h
        result["filename_readback"] = readback
        result["filename_exact_match"] = bool(readback.strip() == target)
    else:
        # UIA fallback typing.
        try:
            edits = [c for c in dialog.descendants() if str(_sig(c).get("control_type", "")) == "Edit"]
        except Exception:
            edits = []
        if edits:
            edit = edits[0]
            try:
                edit.set_focus()
                edit.type_keys("^a{BACKSPACE}", set_foreground=True)
                edit.type_keys(target, with_spaces=True, set_foreground=True)
                result["filename_uia"] = "typed"
                try:
                    rb = str(edit.window_text() or "")
                    result["filename_readback"] = rb
                    result["filename_exact_match"] = bool(rb.strip() == target)
                except Exception:
                    result["filename_exact_match"] = None
            except Exception as exc:
                result["filename_uia"] = f"error:{exc!r}"
        else:
            result["filename_uia"] = "missing_edit"

    # click Save button (primary: common dialog command id 1, locale-agnostic)
    save_clicked = False
    save_row = None
    for r in rows:
        if int(r.get("ctrl_id", -1)) == 1:
            save_row = r
            break
    if save_row is not None and _click_handle(int(save_row.get("handle", 0) or 0)):
        save_clicked = True
        result["save_action"] = {"method": "bm_click_id1", "handle": int(save_row.get("handle", 0) or 0)}

    for caption in ("Save", "Speichern", "&Save", "&Speichern"):
        if quick:
            break
        if save_clicked:
            break
        try:
            btn = dialog.child_window(title=caption, control_type="Button")
            if btn.exists(timeout=0.2):
                try:
                    btn.invoke()
                    save_clicked = True
                    result["save_action"] = {"method": "invoke", "caption": caption}
                    break
                except Exception:
                    btn.click()
                    save_clicked = True
                    result["save_action"] = {"method": "click", "caption": caption}
                    break
        except Exception:
            continue
    if not save_clicked:
        try:
            dialog.type_keys("{ENTER}")
            save_clicked = True
            result["save_action"] = {"method": "enter_fallback"}
        except Exception as exc:
            result["save_action"] = {"method": "failed", "error": repr(exc)}
    return result


def _activate_save_ladder(export_dialog: Any, save_handle: int, vacs_pid: int, main_handle: int) -> List[Dict[str, Any]]:
    attempts: List[Dict[str, Any]] = []
    dialog_handle = int(_sig(export_dialog).get("handle", 0) or 0)
    user32 = ctypes.windll.user32

    def _save_as_now_visible(wait_s: float = 0.4) -> bool:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < float(wait_s):
            if _find_save_as_dialog(int(vacs_pid), int(main_handle), timeout_s=0.05) is not None:
                return True
            time.sleep(0.04)
        return False

    if dialog_handle > 0 and int(save_handle) > 0:
        primary = None
        rows_for_primary = _win32_children(dialog_handle)
        for row in rows_for_primary:
            if int(row.get("handle", 0) or 0) == int(save_handle):
                primary = row
                break
        if primary is None:
            primary = {"handle": int(save_handle), "ctrl_id": 0, "class_name": "unknown", "text": ""}
        try:
            w = Desktop(backend="win32").window(handle=int(save_handle))
            w.click()
            attempts.append({"method": "primary_save_handle_win32_click", "status": "ok", "button_handle": int(save_handle)})
        except Exception as exc:
            attempts.append({"method": "primary_save_handle_win32_click", "status": "error", "error": repr(exc), "button_handle": int(save_handle)})
        action = _press_dialog_button_handle(dialog_handle, primary)
        attempts.append({"method": "primary_save_handle_post", "action": action})
        if _save_as_now_visible(wait_s=2.0):
            attempts.append(
                {
                    "method": "primary_save_handle",
                    "postcheck": "save_as_visible",
                    "button_handle": int(primary.get("handle", 0) or 0),
                    "ctrl_id": int(primary.get("ctrl_id", 0) or 0),
                }
            )
            return attempts

    if dialog_handle > 0:
        candidate_rows = _bitbtn_rows(_win32_children(dialog_handle))
        if not candidate_rows:
            candidate_rows = _sorted_button_rows(_win32_children(dialog_handle))
        for row in candidate_rows[:8]:
            if int(row.get("handle", 0) or 0) == int(save_handle):
                continue
            action = _press_dialog_button_handle(dialog_handle, row)
            attempts.append({"method": "win32_candidate", "action": action})
            if _save_as_now_visible(wait_s=0.35):
                attempts.append(
                    {
                        "method": "win32_candidate",
                        "postcheck": "save_as_visible",
                        "button_handle": int(row.get("handle", 0) or 0),
                        "ctrl_id": int(row.get("ctrl_id", 0) or 0),
                    }
                )
                return attempts

    def _run_one(method: str) -> bool:
        try:
            if method == "win32_click":
                w = Desktop(backend="win32").window(handle=int(save_handle))
                w.click()
                attempts.append({"method": method, "status": "ok"})
            elif method == "bm_click":
                user32.SendMessageW(int(save_handle), BM_CLICK, 0, 0)
                attempts.append({"method": method, "status": "ok"})
            elif method == "wm_command_bn_clicked":
                ctrl_id = int(user32.GetDlgCtrlID(int(save_handle)))
                if dialog_handle > 0 and ctrl_id > 0:
                    wparam = (int(ctrl_id) & 0xFFFF) | ((int(BN_CLICKED) & 0xFFFF) << 16)
                    user32.SendMessageW(int(dialog_handle), WM_COMMAND, int(wparam), int(save_handle))
                    attempts.append({"method": method, "status": "ok", "ctrl_id": ctrl_id})
                else:
                    attempts.append({"method": method, "status": "skipped", "ctrl_id": ctrl_id})
            elif method == "alt_s":
                export_dialog.type_keys("%s", set_foreground=True)
                attempts.append({"method": method, "status": "ok"})
            else:
                export_dialog.type_keys("{ENTER}", set_foreground=True)
                attempts.append({"method": method, "status": "ok"})
        except Exception as exc:
            attempts.append({"method": method, "status": "error", "error": repr(exc)})
            return False
        return True

    for method in ("win32_click", "bm_click", "wm_command_bn_clicked", "alt_s", "enter"):
        ran = _run_one(method)
        if not ran:
            continue
        if _save_as_now_visible():
            attempts.append({"method": method, "postcheck": "save_as_visible"})
            break
    return attempts


def _find_overwrite_dialog(target_pid: int, main_handle: int) -> Optional[Any]:
    for w in _dialog_candidates(int(target_pid), int(main_handle)):
        s = _sig(w)
        class_name = str(s.get("class_name", ""))
        if not re.search(r"(#32770|Dialog|TForm_.*)", class_name, re.IGNORECASE):
            continue
        title = str(s.get("title", "")).lower()
        texts: List[str] = []
        try:
            for c in list(w.descendants())[:60]:
                txt = _window_text(c)
                if txt:
                    texts.append(txt.lower())
        except Exception:
            pass
        haystack = " ".join([title, *texts])
        overwrite_hint = bool(re.search(r"(overwrite|replace|ersetzen|exist|bereits vorhanden|already exists)", haystack, re.IGNORECASE))
        if not overwrite_hint:
            continue
        # require at least one positive action button
        for caption in ("Yes", "Ja", "Replace", "Ersetzen", "OK", "Ok"):
            try:
                btn = w.child_window(title=caption, control_type="Button")
                if btn.exists(timeout=0.05):
                    return w
            except Exception:
                continue
    return None


def _handle_overwrite_confirm(target_pid: int, main_handle: int, timeout_s: float = 3.0) -> Optional[Dict[str, Any]]:
    deadline = time.perf_counter() + float(timeout_s)
    while time.perf_counter() < deadline:
        w = _find_overwrite_dialog(int(target_pid), int(main_handle))
        if w is not None:
            s = _sig(w)
            for caption in ("Yes", "Ja", "Replace", "Ersetzen", "OK", "Ok"):
                try:
                    btn = w.child_window(title=caption, control_type="Button")
                    if btn.exists(timeout=0.1):
                        try:
                            btn.invoke()
                            return {"dialog": s, "action": {"method": "invoke", "caption": caption}}
                        except Exception:
                            btn.click()
                            return {"dialog": s, "action": {"method": "click", "caption": caption}}
                except Exception:
                    continue
        time.sleep(0.15)
    return None


def _unique_export_path(export_root: Path, run_id: str, loop_idx: int, safe_name: str) -> Path:
    base = export_root / f"{run_id}_{loop_idx:02d}_{safe_name}.txt"
    if not base.exists():
        return base
    n = 1
    while True:
        candidate = export_root / f"{run_id}_{loop_idx:02d}_{safe_name}_{n}.txt"
        if not candidate.exists():
            return candidate
        n += 1


def _find_save_bitbtn(export_dialog: Any) -> Optional[Dict[str, Any]]:
    # Primary: non-text win32 button ordering (default style, then top/left).
    win32_rows = _win32_children(int(_sig(export_dialog).get("handle", 0) or 0))
    sorted_buttons = _bitbtn_rows(win32_rows)
    if not sorted_buttons:
        sorted_buttons = _sorted_button_rows(win32_rows)
    if sorted_buttons:
        r = sorted_buttons[0]
        return {
            "handle": int(r.get("handle", 0) or 0),
            "title": r.get("text", ""),
            "class_name": r.get("class_name", ""),
            "ctrl_id": int(r.get("ctrl_id", 0) or 0),
            "style": int(r.get("style", 0) or 0),
        }
    controls = _dialog_controls(export_dialog)
    for c in controls:
        class_name = str(c.get("class_name", "") or "")
        if re.search(r"(trzbitbtn|button)", class_name, re.IGNORECASE):
            return c
    # Last resort (kept for compatibility)
    for c in controls:
        label = (str(c.get("title", "")) + " " + str(c.get("text", ""))).strip().lower()
        class_name = str(c.get("class_name", "") or "")
        if re.search(r"(save|speicher)", label) and re.search(r"(trzbitbtn|button)", class_name, re.IGNORECASE):
            return c
    return None


def _is_graph_child_open(vacs_pid: int, child_handle: int) -> bool:
    main = _find_main(int(vacs_pid))
    if main is None:
        return False
    return any(int(_sig(g).get("handle", 0) or 0) == int(child_handle) for g in _graph_children(main))


def _issue_child_close(child_handle: int, main_handle: int) -> List[Dict[str, Any]]:
    attempts: List[Dict[str, Any]] = []
    def _closed_now() -> bool:
        return not _is_window_alive(int(child_handle))

    try:
        child_uia = Desktop(backend="uia").window(handle=int(child_handle))
        child_uia.set_focus()
        attempts.append({"method": "uia_child_focus", "status": "ok"})
    except Exception as exc:
        attempts.append({"method": "uia_child_focus", "status": "error", "error": repr(exc)})
    # Try explicit child close buttons first (X/Close/Schliessen) before message-based close.
    try:
        child_uia = Desktop(backend="uia").window(handle=int(child_handle))
        close_clicked = False
        for c in child_uia.descendants():
            label = _window_text(c).strip().lower()
            if label not in {"close", "schließen", "schliessen", "x"}:
                continue
            try:
                c.invoke()
                attempts.append({"method": "uia_child_close_button", "status": "ok", "caption": label})
                close_clicked = True
                break
            except Exception:
                try:
                    c.click()
                    attempts.append({"method": "uia_child_close_button", "status": "ok", "caption": label})
                    close_clicked = True
                    break
                except Exception:
                    continue
        if not close_clicked:
            attempts.append({"method": "uia_child_close_button", "status": "missing"})
        else:
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 0.4:
                if _closed_now():
                    return attempts
                time.sleep(0.04)
    except Exception as exc:
        attempts.append({"method": "uia_child_close_button", "status": "error", "error": repr(exc)})
    try:
        child_uia = Desktop(backend="uia").window(handle=int(child_handle))
        child_uia.type_keys("%{F4}", set_foreground=True)
        attempts.append({"method": "uia_child_alt_f4", "status": "ok"})
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 0.35:
            if _closed_now():
                return attempts
            time.sleep(0.04)
    except Exception as exc:
        attempts.append({"method": "uia_child_alt_f4", "status": "error", "error": repr(exc)})
    try:
        ctypes.windll.user32.PostMessageW(int(child_handle), WM_CLOSE, 0, 0)
        attempts.append({"method": "wm_close_post", "status": "ok"})
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 0.25:
            if _closed_now():
                return attempts
            time.sleep(0.04)
    except Exception as exc:
        attempts.append({"method": "wm_close_post", "status": "error", "error": repr(exc)})
    try:
        user32 = ctypes.windll.user32
        mdi = _find_mdi_client_handle(int(main_handle))
        if mdi > 0:
            user32.SendMessageW(int(mdi), WM_MDIACTIVATE, int(child_handle), 0)
            user32.SendMessageW(int(mdi), WM_MDIDESTROY, int(child_handle), 0)
            attempts.append({"method": "mdi_destroy", "status": "ok", "mdi_handle": int(mdi)})
        else:
            attempts.append({"method": "mdi_destroy", "status": "missing_mdi_client"})
    except Exception as exc:
        attempts.append({"method": "mdi_destroy", "status": "error", "error": repr(exc)})
    return attempts


def _close_child_and_context(child_handle: int, vacs_pid: int, main_handle: int) -> Dict[str, Any]:
    result: Dict[str, Any] = {"child_handle": int(child_handle)}
    result["close_attempts"] = _issue_child_close(int(child_handle), int(main_handle))
    handled: List[Dict[str, Any]] = []
    deadline = time.perf_counter() + 5.0
    retries = 0
    while time.perf_counter() < deadline:
        if not _is_graph_child_open(int(vacs_pid), int(child_handle)):
            break
        dialogs = _dialog_candidates(vacs_pid, int(main_handle))
        if not dialogs:
            if retries < 2:
                retries += 1
                handled.append({"retry_close": retries, "attempts": _issue_child_close(int(child_handle), int(main_handle))})
                time.sleep(0.2)
                continue
            break
        any_action = False
        for d in dialogs:
            ds = _sig(d)
            # close only non-graph residual dialogs
            if ds.get("class_name") in CHILD_CLASSES:
                continue
            if ds.get("class_name") == "TForm_Export":
                continue
            if str(ds.get("class_name", "")) == "TForm_Confirm" and _is_graph_child_open(int(vacs_pid), int(child_handle)):
                action = _handle_close_confirm_dialog(d, int(vacs_pid), int(child_handle))
            else:
                action = _close_dialog(d)
            handled.append(
                {
                    "dialog": ds,
                    "dialog_message": _dialog_message_text(d),
                    "dialog_buttons": _dialog_button_labels(d),
                    "action": action,
                }
            )
            any_action = True
        if not any_action:
            if retries < 2:
                retries += 1
                handled.append({"retry_close": retries, "attempts": _issue_child_close(int(child_handle), int(main_handle))})
            else:
                break
        time.sleep(0.15)
    result["context_dialogs"] = handled
    result["closed"] = not _is_graph_child_open(int(vacs_pid), int(child_handle))
    return result


def run_once_safe(args: argparse.Namespace) -> Dict[str, Any]:
    export_root = Path(args.export_dir).resolve()
    export_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir).resolve() / f"run_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    trace_file = out_dir / "trace.jsonl"
    log: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": _now_iso(),
        "export_root": str(export_root),
        "steps": [],
        "trace_file": str(trace_file),
    }
    started_perf = time.perf_counter()

    def step(name: str, **payload: Any) -> None:
        row = {"time": _now_iso(), "step": name, "payload": payload}
        log["steps"].append(row)
        try:
            with trace_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass

    vacs_pid = 0
    if bool(getattr(args, "assume_vacs_ready", False)):
        ready = _select_ready_vacs_pid(timeout_s=min(3.0, float(getattr(args, "save_as_timeout_s", 8.0) or 8.0)))
        step("assume_vacs_ready_scan", **ready)
        selected = dict(ready.get("selected") or {})
        vacs_pid = int(selected.get("pid", 0) or 0)
        if vacs_pid <= 0:
            log["ok"] = False
            log["error"] = "vacs_not_ready_after_f4"
            return log
    else:
        _kill_vacs()
        proc = subprocess.Popen([str(args.vacs_exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        step("start_vacs_mode", mode="prestart_vacs_primary", prestart=True, pid=int(proc.pid), vacs_exe=str(args.vacs_exe))
        time.sleep(0.6)

        interim = _run_interim_with_mode(args, skip_open_via_akabak=False)
        step("interim_reimport_primary", **interim)
        parsed = dict(interim.get("parsed") or {})
        if not bool(parsed.get("ok")):
            interim = _run_interim_with_mode(args, skip_open_via_akabak=True)
            step("interim_reimport_fallback", **interim)
            parsed = dict(interim.get("parsed") or {})
            if not bool(parsed.get("ok")):
                log["ok"] = False
                log["error"] = "interim_reimport_failed"
                return log

        vacs_pid = int(parsed.get("vacs_pid", 0) or 0)
    main = _find_main(vacs_pid)
    if main is None:
        log["ok"] = False
        log["error"] = "vacs_main_missing"
        return log
    main_sig = _sig(main)
    main_handle = int(main_sig.get("handle", 0) or 0)
    step("vacs_main", signature=main_sig)

    exported_files: List[Dict[str, Any]] = []
    per_graph: List[Dict[str, Any]] = []
    processed_handles: set[int] = set()
    seen_export_dialog_handles: set[int] = set()
    max_loops = int(args.max_loops)
    loop_idx = 0
    while loop_idx < max_loops:
        if (time.perf_counter() - started_perf) > float(args.max_runtime_s):
            step("runtime_guard_stop", reason="max_runtime_exceeded", max_runtime_s=float(args.max_runtime_s))
            break
        loop_idx += 1
        main = _find_main(vacs_pid)
        if main is None:
            break
        pre_loop_exports = _close_all_export_dialogs(vacs_pid, int(main_handle), timeout_s=0.9)
        if pre_loop_exports:
            step("pre_loop_export_close", loop=loop_idx, actions=pre_loop_exports)
        drained = _drain_aux_dialogs(vacs_pid, int(main_handle), keep_export_handle=0)
        if drained:
            step("pre_loop_dialog_drain", loop=loop_idx, drained=drained)
        graphs = _graph_children(main)
        graphs_sorted = sorted(graphs, key=lambda g: str(_sig(g).get("title", "")).lower())
        step("graph_snapshot", loop=loop_idx, count=len(graphs_sorted), graphs=[_sig(g) for g in graphs_sorted])
        if not graphs_sorted:
            break
        remaining_targets = [g for g in graphs_sorted if int(_sig(g).get("handle", 0) or 0) not in processed_handles]
        if not remaining_targets:
            step(
                "graph_selection_exhausted",
                loop=loop_idx,
                processed_count=len(processed_handles),
                still_open=[_sig(g) for g in graphs_sorted],
            )
            break
        target = remaining_targets[0]
        t_sig = _sig(target)
        t_handle = int(t_sig.get("handle", 0) or 0)
        row: Dict[str, Any] = {"loop": loop_idx, "target": t_sig}
        step("graph_target_selected", loop=loop_idx, target=t_sig)

        try:
            ok = bool(ctypes.windll.user32.SetForegroundWindow(int(t_handle)))
            row["focus"] = "ok" if ok else "set_foreground_false"
            step("graph_target_focused", loop=loop_idx, handle=t_handle, focus=row["focus"])
        except Exception as exc:
            row["focus"] = f"error:{exc!r}"
            step("graph_target_focus_failed", loop=loop_idx, handle=t_handle, error=repr(exc))
            per_graph.append(row)
            break

        # Open export dialog with ladder.
        trigger_attempts: List[Dict[str, Any]] = []
        export_dialog = None
        for method in ("main_wm_command_52", "target_f7_postmessage", "main_f7_postmessage"):
            try:
                if method == "target_f7_postmessage":
                    _send_f7(int(t_handle))
                elif method == "main_f7_postmessage":
                    _send_f7(int(main_handle))
                else:
                    ctypes.windll.user32.SendMessageW(int(main_handle), WM_COMMAND, int(VACS_EXPORT_COMMAND_ID), 0)
                trigger_attempts.append({"method": method, "status": "ok"})
                export_dialog = _find_data_export_after_trigger(
                    vacs_pid,
                    int(main_handle),
                    timeout_s=0.55,
                    known_handles=seen_export_dialog_handles,
                )
                if export_dialog is not None:
                    trigger_attempts.append({"method": method, "postcheck": "data_export_visible"})
                    break
            except Exception as exc:
                trigger_attempts.append({"method": method, "status": "error", "error": repr(exc)})
        row["export_trigger"] = trigger_attempts
        step("graph_export_triggered", loop=loop_idx, attempts=trigger_attempts)
        if export_dialog is None:
            export_dialog = _find_data_export_after_trigger(
                vacs_pid,
                int(main_handle),
                timeout_s=float(args.dialog_timeout_s),
                known_handles=seen_export_dialog_handles,
            )
        if export_dialog is None:
            row["error"] = "data_export_missing"
            row["wrong_windows"] = [_sig(d) for d in _dialog_candidates(vacs_pid, int(main_handle))]
            step("graph_data_export_missing", loop=loop_idx, wrong_windows=row["wrong_windows"])
            per_graph.append(row)
            break

        exp_sig = _sig(export_dialog)
        exp_handle = int(exp_sig.get("handle", 0) or 0)
        if exp_handle > 0:
            seen_export_dialog_handles.add(exp_handle)
        row["data_export_dialog"] = exp_sig
        step("graph_data_export_found", loop=loop_idx, dialog=exp_sig)
        pre_save_drain = _drain_aux_dialogs(vacs_pid, int(main_handle), keep_export_handle=int(exp_sig.get("handle", 0) or 0))
        if pre_save_drain:
            row["pre_save_dialog_drain"] = pre_save_drain
            step("graph_pre_save_dialog_drain", loop=loop_idx, drained=pre_save_drain)
        if bool(args.capture_export_controls):
            row["data_export_controls"] = _dialog_controls(export_dialog)
            row["data_export_win32_children"] = _win32_children(int(exp_sig.get("handle", 0) or 0))

        save_ctrl = _find_save_bitbtn(export_dialog)
        row["save_control"] = save_ctrl
        if not save_ctrl:
            row["error"] = "save_control_missing"
            step("graph_save_control_missing", loop=loop_idx)
            per_graph.append(row)
            break

        save_handle = int(save_ctrl.get("handle", 0) or 0)
        save_attempts = _activate_save_ladder(export_dialog, save_handle, vacs_pid=vacs_pid, main_handle=int(main_handle))
        row["save_click"] = {"handle": save_handle, "attempts": save_attempts}
        step("graph_save_invoked", loop=loop_idx, save_handle=save_handle, attempts=save_attempts)

        save_as = _find_save_as_dialog(vacs_pid, int(main_handle), timeout_s=float(args.save_as_timeout_s))
        if save_as is None:
            post_save_drain = _drain_aux_dialogs(vacs_pid, int(main_handle), keep_export_handle=int(exp_sig.get("handle", 0) or 0))
            if post_save_drain:
                row["post_save_dialog_drain"] = post_save_drain
                step("graph_post_save_dialog_drain", loop=loop_idx, drained=post_save_drain)
            save_as = _find_save_as_dialog(vacs_pid, int(main_handle), timeout_s=3.0)
        if save_as is None:
            overwrite = _handle_overwrite_confirm(vacs_pid, int(main_handle), timeout_s=2.0)
            if overwrite is not None:
                row["overwrite_confirm_without_save_as"] = overwrite
                step("graph_overwrite_without_save_as", loop=loop_idx, overwrite=overwrite)
                # In this branch the app saved using previously remembered path.
                # Try to discover newest TXT as evidence.
                latest_txt = None
                latest_mtime = 0.0
                for p in export_root.glob("*.txt"):
                    try:
                        mt = p.stat().st_mtime
                    except Exception:
                        continue
                    if mt > latest_mtime:
                        latest_mtime = mt
                        latest_txt = p
                if latest_txt is not None:
                    target_file = latest_txt
                    row["recovered_target_file"] = str(latest_txt)
                else:
                    row["error"] = "save_as_missing_and_no_recoverable_target"
                    row["dialogs_after_save"] = [
                        {
                            "signature": _sig(d),
                            "message": _dialog_message_text(d),
                            "buttons": _dialog_button_labels(d),
                        }
                        for d in _dialog_candidates(vacs_pid, int(main_handle))
                    ]
                    step("graph_save_as_missing_unrecoverable", loop=loop_idx, dialogs_after_save=row["dialogs_after_save"])
                    per_graph.append(row)
                    break
            else:
                row["error"] = "save_as_dialog_missing"
                row["dialogs_after_save"] = [
                    {
                        "signature": _sig(d),
                        "message": _dialog_message_text(d),
                        "buttons": _dialog_button_labels(d),
                    }
                    for d in _dialog_candidates(vacs_pid, int(main_handle))
                ]
                step("graph_save_as_missing", loop=loop_idx, dialogs_after_save=row["dialogs_after_save"])
                per_graph.append(row)
                break
        else:
            save_as_sig = _sig(save_as)
            row["save_as_dialog"] = save_as_sig
            step("graph_save_as_found", loop=loop_idx, dialog=save_as_sig)

            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(t_sig.get("title", "graph")).strip()).strip("_")
            if not safe_name:
                safe_name = f"graph_{loop_idx}"
            target_file = _unique_export_path(export_root, run_id, loop_idx, safe_name)
            set_path_res = _set_save_path(save_as, target_file)
            row["save_as_set_path"] = set_path_res
            step("graph_save_as_path_set", loop=loop_idx, target=str(target_file), set_path=set_path_res)

            overwrite = _handle_overwrite_confirm(vacs_pid, int(main_handle), timeout_s=2.0)
            if overwrite is not None:
                row["overwrite_confirm"] = overwrite
                step("graph_overwrite_confirm", loop=loop_idx, overwrite=overwrite)

        # Postcondition: file exists with content.
        deadline = time.perf_counter() + float(args.file_timeout_s)
        while time.perf_counter() < deadline and (not target_file.exists() or target_file.stat().st_size <= 0):
            time.sleep(0.15)
        file_ok = target_file.exists() and target_file.stat().st_size > 0
        row["file_postcondition"] = {
            "path": str(target_file),
            "exists": bool(target_file.exists()),
            "bytes": int(target_file.stat().st_size) if target_file.exists() else 0,
            "ok": bool(file_ok),
        }
        if not file_ok:
            row["error"] = "export_file_missing_or_empty"
            step("graph_export_file_failed", loop=loop_idx, file_postcondition=row["file_postcondition"])
            per_graph.append(row)
            break

        exported_files.append({"graph": t_sig, "path": str(target_file), "bytes": int(target_file.stat().st_size)})
        step("graph_export_file_ok", loop=loop_idx, file=row["file_postcondition"])

        # Close Data Export window.
        row["close_data_export"] = _close_dialog(export_dialog)
        step("graph_data_export_closed", loop=loop_idx, close=row["close_data_export"])
        row["close_all_export_dialogs"] = _close_all_export_dialogs(vacs_pid, int(main_handle), timeout_s=1.0)
        if row["close_all_export_dialogs"]:
            step("graph_close_all_export_dialogs", loop=loop_idx, actions=row["close_all_export_dialogs"])
        row["dialog_quiescence_after_export"] = _wait_for_dialog_quiescence(vacs_pid, int(main_handle), timeout_s=2.0, stable_s=0.25)
        if not bool(row["dialog_quiescence_after_export"].get("quiescent")):
            step("graph_quiescence_after_export_timeout", loop=loop_idx, details=row["dialog_quiescence_after_export"])

        # Close target child window with context handling.
        row["close_child"] = _close_child_and_context(t_handle, vacs_pid, int(main_handle))
        step("graph_child_closed_request", loop=loop_idx, close_child=row["close_child"])
        row["post_close_all_export_dialogs"] = _close_all_export_dialogs(vacs_pid, int(main_handle), timeout_s=0.9)
        if row["post_close_all_export_dialogs"]:
            step("graph_post_close_all_export_dialogs", loop=loop_idx, actions=row["post_close_all_export_dialogs"])
        row["dialog_quiescence_after_child_close"] = _wait_for_dialog_quiescence(vacs_pid, int(main_handle), timeout_s=2.0, stable_s=0.25)
        if not bool(row["dialog_quiescence_after_child_close"].get("quiescent")):
            step("graph_quiescence_after_child_close_timeout", loop=loop_idx, details=row["dialog_quiescence_after_child_close"])

        # verify child closed
        time.sleep(0.05)
        main_after = _find_main(vacs_pid)
        if main_after is not None:
            still_open = any(int(_sig(g).get("handle", 0) or 0) == t_handle for g in _graph_children(main_after))
        else:
            still_open = False
        row["child_closed_postcondition"] = {"handle": t_handle, "closed": not still_open}
        step("graph_child_closed_postcondition", loop=loop_idx, post=row["child_closed_postcondition"])

        per_graph.append(row)
        processed_handles.add(int(t_handle))
        if still_open:
            step("graph_child_still_open_continue", loop=loop_idx, handle=t_handle)
            continue

    # Final graph count
    main_end = _find_main(vacs_pid)
    remaining = [_sig(g) for g in _graph_children(main_end)] if main_end is not None else []
    step("remaining_graphs", count=len(remaining), graphs=remaining)
    log["per_graph"] = per_graph
    log["exported_files"] = exported_files
    log["remaining_graphs"] = remaining
    log["ok"] = bool(len(remaining) == 0 and len(exported_files) > 0)

    # close vacs
    _kill_vacs()
    step("kill_vacs_final")
    log["finished_at"] = _now_iso()

    out_file = out_dir / "summary.json"
    out_file.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log["summary_file"] = str(out_file)
    return log


def _copy_args_with(args: argparse.Namespace, **overrides: Any) -> argparse.Namespace:
    data = dict(vars(args))
    data.update(overrides)
    return argparse.Namespace(**data)


def run_once_fast(args: argparse.Namespace) -> Dict[str, Any]:
    export_root = Path(args.export_dir).resolve()
    export_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir).resolve() / f"run_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    trace_file = out_dir / "trace.jsonl"
    log: Dict[str, Any] = {
        "mode": "fast",
        "run_id": run_id,
        "started_at": _now_iso(),
        "export_root": str(export_root),
        "steps": [],
        "trace_file": str(trace_file),
    }

    def step(name: str, **payload: Any) -> None:
        row = {"time": _now_iso(), "step": name, "payload": payload}
        log["steps"].append(row)
        try:
            with trace_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass

    vacs_pid = 0
    ready_selected: Dict[str, Any] = {}
    if bool(getattr(args, "assume_vacs_ready", False)):
        quick_timeout_s = min(
            FAST_ASSUME_READY_QUICK_TIMEOUT_S,
            max(FAST_PRE_EXPORT_GRAPH_READY_TIMEOUT_S, float(getattr(args, "save_as_timeout_s", 8.0) or 8.0)),
        )
        ready_quick = _select_ready_vacs_pid(
            timeout_s=quick_timeout_s,
            poll_s=FAST_PRE_EXPORT_GRAPH_POLL_S,
        )
        step("assume_vacs_ready_scan_quick", timeout_s=quick_timeout_s, **ready_quick)
        selected = dict(ready_quick.get("selected") or {})
        if not selected:
            fallback_timeout_s = max(FAST_PRE_EXPORT_GRAPH_READY_TIMEOUT_S, FAST_ASSUME_READY_TIMEOUT_S)
            ready = _select_ready_vacs_pid(
                timeout_s=fallback_timeout_s,
                poll_s=FAST_PRE_EXPORT_GRAPH_POLL_S,
            )
            step("assume_vacs_ready_scan_fallback", timeout_s=fallback_timeout_s, **ready)
            selected = dict(ready.get("selected") or {})
        ready_selected = dict(selected)
        vacs_pid = int(selected.get("pid", 0) or 0)
        if vacs_pid <= 0:
            log["ok"] = False
            log["error"] = "vacs_not_ready_after_f4"
            out_file = out_dir / "summary.json"
            out_file.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            log["summary_file"] = str(out_file)
            return log
    else:
        _kill_vacs()
        step("kill_vacs_prestart")
        proc = subprocess.Popen([str(args.vacs_exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        step("start_vacs_mode", mode="fast_prestart_vacs_primary", prestart=True, pid=int(proc.pid), vacs_exe=str(args.vacs_exe))
        time.sleep(0.08)

    fast_interim_args = _copy_args_with(
        args,
        interim_timeout_s=min(int(getattr(args, "interim_timeout_s", 90) or 90), 24),
        interim_idle_timeout_s=min(int(getattr(args, "interim_idle_timeout_s", 20) or 20), 9),
        interim_startup_timeout_s=min(int(getattr(args, "interim_startup_timeout_s", 25) or 25), 8),
    )
    reentry_interim_args = _copy_args_with(
        args,
        interim_timeout_s=min(int(getattr(args, "interim_timeout_s", 90) or 90), 35),
        interim_idle_timeout_s=min(int(getattr(args, "interim_idle_timeout_s", 20) or 20), 12),
        interim_startup_timeout_s=min(int(getattr(args, "interim_startup_timeout_s", 25) or 25), 12),
    )
    relaxed_interim_args = _copy_args_with(
        args,
        interim_timeout_s=min(int(getattr(args, "interim_timeout_s", 90) or 90), 35),
        interim_idle_timeout_s=min(int(getattr(args, "interim_idle_timeout_s", 20) or 20), 12),
        interim_startup_timeout_s=min(int(getattr(args, "interim_startup_timeout_s", 25) or 25), 12),
    )

    if not bool(getattr(args, "assume_vacs_ready", False)):
        # Fast primary: AKABAK menu handshake path (observed as most stable on this VM).
        interim = _run_interim_with_mode(fast_interim_args, skip_open_via_akabak=False)
        step("interim_reimport_primary", **interim)
        parsed = dict(interim.get("parsed") or {})
        if not bool(parsed.get("ok")):
            # Fast accept path: if graphs are already present in VACS, skip second interim run.
            vacs_pid_hint = int(parsed.get("vacs_pid", 0) or 0)
            graph_count_hint = 0
            if vacs_pid_hint > 0:
                main_hint = _find_main_fast(vacs_pid_hint)
                try:
                    graph_count_hint = len(_graph_children_fast(vacs_pid_hint, main_hint=main_hint))
                except Exception:
                    graph_count_hint = 0
            if graph_count_hint > 0:
                parsed["ok"] = True
                parsed["accepted_existing_graphs"] = True
                parsed["accepted_graph_count"] = int(graph_count_hint)
                step(
                    "interim_reimport_primary_accepted_existing_graphs",
                    vacs_pid=vacs_pid_hint,
                    graph_count=graph_count_hint,
                    reason=str(parsed.get("error", "")),
                )

        if not bool(parsed.get("ok")):
            # Reentry point: attach-only retry against existing VACS instance.
            interim = _run_interim_with_mode(reentry_interim_args, skip_open_via_akabak=True)
            step("interim_reimport_reentry_attach_only", **interim)
            parsed = dict(interim.get("parsed") or {})
            if not bool(parsed.get("ok")):
                # Final fallback: menu handshake with relaxed timeout budget.
                interim = _run_interim_with_mode(relaxed_interim_args, skip_open_via_akabak=False)
                step("interim_reimport_fallback_open_via_akabak", **interim)
                parsed = dict(interim.get("parsed") or {})

        if not bool(parsed.get("ok")):
            # Late accept path: attach fallback may have produced visible graph windows despite timeout text.
            vacs_pid_hint = int(parsed.get("vacs_pid", 0) or 0)
            graph_count_hint = 0
            if vacs_pid_hint > 0:
                main_hint = _find_main_fast(vacs_pid_hint)
                try:
                    graph_count_hint = len(_graph_children_fast(vacs_pid_hint, main_hint=main_hint))
                except Exception:
                    graph_count_hint = 0
            if graph_count_hint > 0:
                parsed["ok"] = True
                parsed["accepted_existing_graphs"] = True
                parsed["accepted_graph_count"] = int(graph_count_hint)
                step(
                    "interim_reimport_fallback_accepted_existing_graphs",
                    vacs_pid=vacs_pid_hint,
                    graph_count=graph_count_hint,
                    reason=str(parsed.get("error", "")),
                )

        if not bool(parsed.get("ok")):
            log["ok"] = False
            log["error"] = "interim_reimport_failed"
            out_file = out_dir / "summary.json"
            out_file.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            log["summary_file"] = str(out_file)
            return log

        vacs_pid = int(parsed.get("vacs_pid", 0) or 0)
    main = _find_main_fast(vacs_pid)
    if main is None:
        log["ok"] = False
        log["error"] = "vacs_main_missing"
        out_file = out_dir / "summary.json"
        out_file.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log["summary_file"] = str(out_file)
        return log
    main_sig = _sig(main)
    main_handle = int(main_sig.get("handle", 0) or 0)
    step("vacs_main", signature=main_sig)

    seed_graph_count = int(ready_selected.get("graph_count", 0) or 0)
    graphs: List[Any] = []
    if seed_graph_count > 0:
        try:
            graphs = _graph_children_fast(int(vacs_pid), main_hint=main)
        except Exception:
            graphs = []
        step(
            "graph_seed_from_ready_scan",
            seed_graph_count=seed_graph_count,
            fetched_count=len(graphs),
            graph_titles=[str(_sig(g).get("title", "") or "") for g in list(graphs)[:12]],
        )

    if not graphs:
        ready_state = _wait_initial_graphs_fast(
            int(vacs_pid),
            timeout_s=min(FAST_PRE_EXPORT_GRAPH_READY_TIMEOUT_S, float(getattr(args, "dialog_timeout_s", 5.0) or 5.0)),
        )
        main = ready_state.get("main") or main
        graphs = list(ready_state.get("graphs", []) or [])
    if not graphs and main is not None:
        graphs = _graph_children_fast(int(vacs_pid), main_hint=main)

    stabilized: Dict[str, Any]
    if seed_graph_count > 0 and graphs:
        stabilized = {
            "main": main,
            "graphs": list(graphs),
            "observed_counts": [int(len(graphs))],
            "stable_elapsed_s": 0.0,
            "seed_shortcut": True,
        }
        step(
            "graph_snapshot_seed_shortcut",
            count=len(graphs),
            observed_counts=list(stabilized.get("observed_counts", []) or []),
            stable_elapsed_s=0.0,
        )
    else:
        quick_stabilized = _collect_graphs_until_stable_fast(
            int(vacs_pid),
            main_hint=main,
            initial_graphs=graphs,
            timeout_s=min(
                FAST_GRAPH_STABILIZE_QUICK_TIMEOUT_S,
                max(0.2, float(getattr(args, "dialog_timeout_s", 5.0) or 5.0)),
            ),
            stable_for_s=FAST_GRAPH_STABLE_FOR_QUICK_S,
        )
        main = quick_stabilized.get("main") or main
        graphs = list(quick_stabilized.get("graphs", []) or graphs)
        step(
            "graph_snapshot_stabilized_quick",
            count=len(graphs),
            observed_counts=list(quick_stabilized.get("observed_counts", []) or []),
            stable_elapsed_s=float(quick_stabilized.get("stable_elapsed_s", 0.0) or 0.0),
        )
        stabilized = quick_stabilized

    if not graphs:
        stabilized = _collect_graphs_until_stable_fast(
            int(vacs_pid),
            main_hint=main,
            initial_graphs=graphs,
            timeout_s=min(FAST_GRAPH_STABILIZE_TIMEOUT_S, max(0.6, float(getattr(args, "dialog_timeout_s", 5.0) or 5.0))),
            stable_for_s=FAST_GRAPH_STABLE_FOR_S,
        )
        main = stabilized.get("main") or main
        graphs = list(stabilized.get("graphs", []) or graphs)
        step(
            "graph_snapshot_stabilized_fallback",
            count=len(graphs),
            observed_counts=list(stabilized.get("observed_counts", []) or []),
            stable_elapsed_s=float(stabilized.get("stable_elapsed_s", 0.0) or 0.0),
        )

    graphs_sorted = sorted(graphs, key=lambda g: str(_sig(g).get("title", "")).lower())
    step(
        "graph_snapshot_stabilized",
        count=len(graphs_sorted),
        observed_counts=list(stabilized.get("observed_counts", []) or []),
        stable_elapsed_s=float(stabilized.get("stable_elapsed_s", 0.0) or 0.0),
        graph_titles=[str(_sig(g).get("title", "") or "") for g in list(graphs_sorted)[:12]],
    )
    step(
        "graph_snapshot_initial",
        count=len(graphs_sorted),
        graph_titles=[str(_sig(g).get("title", "") or "") for g in list(graphs_sorted)[:12]],
    )
    if not graphs_sorted:
        log["ok"] = False
        log["error"] = "no_graph_windows"
        out_file = out_dir / "summary.json"
        out_file.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log["summary_file"] = str(out_file)
        return log

    exported_files: List[Dict[str, Any]] = []
    per_graph: List[Dict[str, Any]] = []
    seen_export_dialog_handles: set[int] = set()
    max_graphs = min(len(graphs_sorted), int(args.max_loops))

    user32 = ctypes.windll.user32
    mdi_handle = _find_mdi_client_handle(int(main_handle))

    for loop_idx, target in enumerate(graphs_sorted[:max_graphs], start=1):
        t_sig = _sig(target)
        t_handle = int(t_sig.get("handle", 0) or 0)
        row: Dict[str, Any] = {"loop": loop_idx, "target": t_sig}
        step("graph_target_selected", loop=loop_idx, target=t_sig)

        try:
            if mdi_handle > 0 and t_handle > 0:
                user32.SendMessageW(int(mdi_handle), WM_MDIACTIVATE, int(t_handle), 0)
            user32.SetForegroundWindow(int(t_handle))
            row["focus"] = "ok"
        except Exception as exc:
            row["focus"] = f"error:{exc!r}"
        step("graph_target_focused", loop=loop_idx, handle=t_handle, focus=row["focus"])

        trigger_attempts: List[Dict[str, Any]] = []
        export_dialog = None
        for method in ("main_wm_command_52", "target_f7_postmessage", "main_f7_postmessage"):
            try:
                if method == "main_wm_command_52":
                    user32.SendMessageW(int(main_handle), WM_COMMAND, int(VACS_EXPORT_COMMAND_ID), 0)
                elif method == "target_f7_postmessage":
                    _send_f7(int(t_handle))
                else:
                    _send_f7(int(main_handle))
                trigger_attempts.append({"method": method, "status": "ok"})
                export_dialog = _find_data_export_after_trigger_fast(vacs_pid, timeout_s=0.28, known_handles=seen_export_dialog_handles)
                if export_dialog is not None:
                    trigger_attempts.append({"method": method, "postcheck": "data_export_visible"})
                    break
            except Exception as exc:
                trigger_attempts.append({"method": method, "status": "error", "error": repr(exc)})
        row["export_trigger"] = trigger_attempts
        step("graph_export_triggered", loop=loop_idx, attempts=trigger_attempts)

        if export_dialog is None:
            export_dialog = _find_data_export_after_trigger_fast(vacs_pid, timeout_s=0.65, known_handles=seen_export_dialog_handles)
        if export_dialog is None:
            row["error"] = "data_export_missing"
            row["wrong_windows"] = [_sig(d) for d in _dialog_candidates(vacs_pid, int(main_handle))]
            step("graph_data_export_missing", loop=loop_idx, wrong_windows=row["wrong_windows"])
            per_graph.append(row)
            break

        exp_sig = _sig(export_dialog)
        exp_handle = int(exp_sig.get("handle", 0) or 0)
        if exp_handle > 0:
            seen_export_dialog_handles.add(exp_handle)
        row["data_export_dialog"] = exp_sig
        step("graph_data_export_found", loop=loop_idx, dialog=exp_sig)

        save_ctrl = _find_save_bitbtn(export_dialog)
        row["save_control"] = save_ctrl
        if not save_ctrl:
            row["error"] = "save_control_missing"
            step("graph_save_control_missing", loop=loop_idx)
            per_graph.append(row)
            break

        save_handle = int(save_ctrl.get("handle", 0) or 0)
        save_attempts: List[Dict[str, Any]] = []
        try:
            w = Desktop(backend="win32").window(handle=int(save_handle))
            w.click()
            save_attempts.append({"method": "primary_save_handle_win32_click", "status": "ok", "button_handle": int(save_handle)})
        except Exception as exc:
            save_attempts.append({"method": "primary_save_handle_win32_click", "status": "error", "error": repr(exc), "button_handle": int(save_handle)})
            try:
                user32.SendMessageW(int(save_handle), BM_CLICK, 0, 0)
                save_attempts.append({"method": "primary_save_handle_bm_click", "status": "ok", "button_handle": int(save_handle)})
            except Exception as exc2:
                save_attempts.append({"method": "primary_save_handle_bm_click", "status": "error", "error": repr(exc2), "button_handle": int(save_handle)})
        row["save_click"] = {"handle": save_handle, "attempts": save_attempts}
        step("graph_save_invoked", loop=loop_idx, save_handle=save_handle, attempts=save_attempts)

        save_as = _find_save_as_dialog_fast(vacs_pid, timeout_s=min(float(args.save_as_timeout_s), 1.8))
        if save_as is None:
            row["error"] = "save_as_dialog_missing"
            row["dialogs_after_save"] = [
                {"signature": _sig(d), "message": _dialog_message_text(d), "buttons": _dialog_button_labels(d)}
                for d in _dialog_candidates(vacs_pid, int(main_handle))
            ]
            step("graph_save_as_missing", loop=loop_idx, dialogs_after_save=row["dialogs_after_save"])
            per_graph.append(row)
            break

        save_as_sig = _sig(save_as)
        row["save_as_dialog"] = save_as_sig
        step("graph_save_as_found", loop=loop_idx, dialog=save_as_sig)

        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(t_sig.get("title", "graph")).strip()).strip("_")
        if not safe_name:
            safe_name = f"graph_{loop_idx}"
        target_file = _unique_export_path(export_root, run_id, loop_idx, safe_name)
        set_path_res = _set_save_path(save_as, target_file, quick=True)
        row["save_as_set_path"] = set_path_res
        step("graph_save_as_path_set", loop=loop_idx, target=str(target_file), set_path=set_path_res)

        deadline = time.perf_counter() + min(float(args.file_timeout_s), 1.2)
        while time.perf_counter() < deadline and (not target_file.exists() or target_file.stat().st_size <= 0):
            time.sleep(0.03)
        file_ok = target_file.exists() and target_file.stat().st_size > 0
        row["file_postcondition"] = {
            "path": str(target_file),
            "exists": bool(target_file.exists()),
            "bytes": int(target_file.stat().st_size) if target_file.exists() else 0,
            "ok": bool(file_ok),
        }
        if not file_ok:
            row["error"] = "export_file_missing_or_empty"
            step("graph_export_file_failed", loop=loop_idx, file_postcondition=row["file_postcondition"])
            per_graph.append(row)
            break
        exported_files.append({"graph": t_sig, "path": str(target_file), "bytes": int(target_file.stat().st_size)})
        step("graph_export_file_ok", loop=loop_idx, file=row["file_postcondition"])

        # Fast path: close export dialog only; do not close graph child per iteration.
        try:
            if exp_handle > 0:
                user32.PostMessageW(int(exp_handle), WM_CLOSE, 0, 0)
                closed = False
                t0 = time.perf_counter()
                while time.perf_counter() - t0 < 0.25:
                    if not _is_window_alive(exp_handle):
                        closed = True
                        break
                    time.sleep(0.02)
                if not closed:
                    row["close_data_export"] = {
                        "status": "partial",
                        "method": "wm_close_post_timeout",
                        "handle": exp_handle,
                    }
                    try:
                        row["close_data_export_fallback"] = _close_dialog(export_dialog)
                    except Exception as exc:
                        row["close_data_export_fallback"] = {"status": "error", "error": repr(exc)}
                else:
                    row["close_data_export"] = {"status": "ok", "method": "wm_close_post", "handle": exp_handle}
            else:
                row["close_data_export"] = _close_dialog(export_dialog)
        except Exception as exc:
            row["close_data_export"] = {"status": "error", "error": repr(exc)}
        step("graph_data_export_closed", loop=loop_idx, close=row["close_data_export"])

        confirm_drain = _drain_aux_dialogs_fast(vacs_pid)
        if confirm_drain:
            row["confirm_drain"] = confirm_drain
            step("graph_confirm_drained", loop=loop_idx, count=len(confirm_drain), rows=confirm_drain)

        per_graph.append(row)

    main_end = _find_main_fast(vacs_pid)
    remaining_graphs = _graph_children_fast(int(vacs_pid), main_hint=main_end) if main_end is not None else []
    remaining = [_sig(g) for g in remaining_graphs]
    step(
        "remaining_graphs",
        count=len(remaining),
        graph_titles=[str(row.get("title", "") or "") for row in remaining[:12]],
    )
    log["per_graph"] = per_graph
    log["exported_files"] = exported_files
    log["remaining_graphs"] = remaining
    log["ok"] = bool(len(exported_files) >= max_graphs and max_graphs > 0)

    _kill_vacs()
    step("kill_vacs_final")
    log["finished_at"] = _now_iso()
    out_file = out_dir / "summary.json"
    out_file.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log["summary_file"] = str(out_file)
    return log


def run_once(args: argparse.Namespace) -> Dict[str, Any]:
    mode = str(getattr(args, "mode", "auto") or "auto").lower()
    if mode == "safe":
        return run_once_safe(args)
    if mode == "fast":
        return run_once_fast(args)

    fast = run_once_fast(args)
    if bool(fast.get("ok")):
        fast["fallback_used"] = False
        return fast
    safe_primary_args = _copy_args_with(args, mode="safe")
    safe = run_once_safe(safe_primary_args)
    if bool(safe.get("ok")):
        safe["fallback_used"] = True
        safe["fallback_reason"] = "fast_mode_failed"
        safe["safe_args_overrides"] = {"mode": "safe"}
        safe["fast_failure"] = {
            "error": fast.get("error"),
            "summary_file": fast.get("summary_file"),
            "run_id": fast.get("run_id"),
        }
        return safe

    # Optional rescue: full interim reimport path.
    # Disabled by default because AKABAK is typically closed at this stage in the
    # runtime pipeline, so this branch is usually non-functional and only adds delay.
    if bool(getattr(args, "assume_vacs_ready", False)) and bool(getattr(args, "allow_interim_rescue", False)):
        rescue_args = _copy_args_with(args, mode="safe", assume_vacs_ready=False)
        rescue = run_once_safe(rescue_args)
        if bool(rescue.get("ok")):
            rescue["fallback_used"] = True
            rescue["fallback_reason"] = "fast_mode_failed_then_safe_rescue"
            rescue["safe_args_overrides"] = {"mode": "safe", "assume_vacs_ready": False}
            rescue["fast_failure"] = {
                "error": fast.get("error"),
                "summary_file": fast.get("summary_file"),
                "run_id": fast.get("run_id"),
            }
            rescue["safe_primary_failure"] = {
                "error": safe.get("error"),
                "summary_file": safe.get("summary_file"),
                "run_id": safe.get("run_id"),
            }
            return rescue
        safe["safe_rescue_failure"] = {
            "error": rescue.get("error"),
            "summary_file": rescue.get("summary_file"),
            "run_id": rescue.get("run_id"),
        }
    elif bool(getattr(args, "assume_vacs_ready", False)):
        safe["safe_rescue_skipped"] = {
            "reason": "disabled_by_default",
            "toggle": "--allow-interim-rescue",
        }

    safe["fallback_used"] = True
    safe["fallback_reason"] = "fast_mode_failed"
    safe["safe_args_overrides"] = {"mode": "safe"}
    safe["fast_failure"] = {
        "error": fast.get("error"),
        "summary_file": fast.get("summary_file"),
        "run_id": fast.get("run_id"),
    }
    return safe


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Export and save all VACS graph child windows.")
    p.add_argument("--mode", choices=("auto", "fast", "safe"), default="auto", help="auto=fast-first then safe fallback")
    p.add_argument("--akabak-exe", required=True)
    p.add_argument("--vacs-exe", required=True)
    p.add_argument(
        "--assume-vacs-ready",
        action="store_true",
        help="Assume VACS is already opened by AKABAK F4 and skip interim reimport/open flow.",
    )
    p.add_argument(
        "--allow-interim-rescue",
        action="store_true",
        help="Allow final auto-mode rescue branch that disables --assume-vacs-ready and runs interim reimport.",
    )
    p.add_argument("--export-dir", required=True, help="Target folder under C:\\Horns\\... for this version")
    p.add_argument("--output-dir", default="runner_test_workspace/logs/vacs_export_save_all")
    p.add_argument("--dialog-timeout-s", type=float, default=5.0)
    p.add_argument("--save-as-timeout-s", type=float, default=8.0)
    p.add_argument("--file-timeout-s", type=float, default=8.0)
    p.add_argument("--capture-export-controls", action="store_true", help="Capture full Data Export control dumps (slower).")
    p.add_argument("--max-runtime-s", type=float, default=420.0)
    p.add_argument("--max-loops", type=int, default=12)
    p.add_argument("--interim-timeout-s", type=int, default=90)
    p.add_argument("--interim-idle-timeout-s", type=int, default=20)
    p.add_argument("--interim-startup-timeout-s", type=int, default=25)
    return p


def main() -> int:
    from app.audit_mode import enable_audit_mode

    enable_audit_mode(entrypoint="scripts.vacs_export_save_all.main")
    args = build_parser().parse_args()
    result = run_once(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

