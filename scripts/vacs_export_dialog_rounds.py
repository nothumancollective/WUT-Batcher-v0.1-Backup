"""Run 5 deterministic VACS export-dialog discovery rounds.

Flow per round:
1) Start VACS process.
2) Re-import graphs from AKABAK via existing interim script (F7 handoff).
3) Select target child window and optional pre-action (pitfall trigger).
4) Trigger export (F7) and inspect "Data Export" dialog controls.
5) Close dialogs and VACS (No save) to reset for next round.
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

from pywinauto import Desktop


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


ROUND_RECIPES: List[Dict[str, Any]] = [
    {"round_id": "r1_datgraph_basic", "target_class": "TForm_DatGraph", "pre_action_key": None, "note": "baseline graph window"},
    {"round_id": "r2_datcontour_basic", "target_class": "TForm_DatContour", "pre_action_key": None, "note": "baseline contour window"},
    {"round_id": "r3_graph_range_probe", "target_class": "TForm_DatGraph", "pre_action_key": "^r", "note": "probe Graph Range pitfall"},
    {"round_id": "r4_legends_probe", "target_class": "TForm_DatGraph", "pre_action_key": "{F2}", "note": "probe legends dialog pitfall"},
    {"round_id": "r5_properties_probe", "target_class": "TForm_DatGraph", "pre_action_key": "{F3}", "note": "probe properties dialog pitfall"},
]

CHILD_CLASSES = {"TForm_DatGraph", "TForm_DatContour", "TForm_Editor"}
WM_COMMAND = 0x0111
VACS_EXPORT_COMMAND_ID = 52


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_text(ctrl: Any) -> str:
    try:
        return str(ctrl.window_text() or "").strip()
    except Exception:
        try:
            return str(getattr(ctrl.element_info, "name", "") or "").strip()
        except Exception:
            return ""


def _signature(ctrl: Any) -> Dict[str, Any]:
    info = getattr(ctrl, "element_info", None)
    return {
        "handle": int(getattr(info, "handle", 0) or 0),
        "title": str(getattr(info, "name", "") or ""),
        "class_name": str(getattr(info, "class_name", "") or ""),
        "control_type": str(getattr(info, "control_type", "") or ""),
        "automation_id": str(getattr(info, "automation_id", "") or ""),
        "process_id": int(getattr(info, "process_id", 0) or 0),
    }


def _collect_windows_for_pid(pid: int) -> List[Any]:
    try:
        return list(Desktop(backend="uia").windows(process=int(pid)))
    except Exception:
        return []


def _find_vacs_main_window(pid: int) -> Optional[Any]:
    for w in _collect_windows_for_pid(pid):
        sig = _signature(w)
        if sig.get("class_name") == "TForm_DatMain":
            return w
    return None


def _collect_child_windows(main_window: Any) -> List[Any]:
    rows: Dict[int, Any] = {}
    for c in main_window.descendants():
        sig = _signature(c)
        if sig.get("control_type") != "Window":
            continue
        if sig.get("class_name") not in CHILD_CLASSES:
            continue
        handle = int(sig.get("handle", 0) or 0)
        if handle > 0:
            rows[handle] = c
    return list(rows.values())


def _find_dialog_candidates(pid: int, *, main_handle: int) -> List[Any]:
    rows: List[Any] = []
    for w in _collect_windows_for_pid(pid):
        sig = _signature(w)
        handle = int(sig.get("handle", 0) or 0)
        if handle <= 0 or handle == int(main_handle):
            continue
        rows.append(w)
    main = _find_vacs_main_window(pid)
    if main is not None:
        for c in main.descendants():
            sig = _signature(c)
            if sig.get("control_type") != "Window":
                continue
            handle = int(sig.get("handle", 0) or 0)
            if handle <= 0 or handle == int(main_handle):
                continue
            if sig.get("class_name") in CHILD_CLASSES:
                continue
            rows.append(c)
    uniq: Dict[int, Any] = {}
    for w in rows:
        sig = _signature(w)
        handle = int(sig.get("handle", 0) or 0)
        if handle > 0:
            uniq[handle] = w
    return list(uniq.values())


def _find_data_export_dialog(pid: int, *, main_handle: int) -> Optional[Any]:
    for w in _find_dialog_candidates(pid, main_handle=main_handle):
        sig = _signature(w)
        title = str(sig.get("title", ""))
        class_name = str(sig.get("class_name", ""))
        if re.search(r"data\s*export", title, re.IGNORECASE):
            return w
        if re.search(r"(export\s*data|data\s*io)", title, re.IGNORECASE):
            return w
        if re.search(r"(export)", title, re.IGNORECASE) and re.search(r"(TForm|#32770|Dialog)", class_name, re.IGNORECASE):
            return w
    return None


def _dialog_controls(window: Any, limit: int = 120) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for c in list(window.descendants())[:limit]:
        sig = _signature(c)
        sig["text"] = _window_text(c)
        rows.append(sig)
    return rows


def _find_save_button(controls: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for c in controls:
        label = (str(c.get("title", "")) + " " + str(c.get("text", ""))).strip().lower()
        class_name = str(c.get("class_name", "") or "")
        control_type = str(c.get("control_type", "") or "")
        if re.search(r"(save|speicher)", label) and (
            control_type in {"Button", "Pane"} or re.search(r"(TRzBitBtn|Button)", class_name, re.IGNORECASE)
        ):
            return c
    return None


def _win32_child_dump(window_handle: int) -> List[Dict[str, Any]]:
    hwnd = int(window_handle or 0)
    if hwnd <= 0:
        return []
    user32 = ctypes.windll.user32
    get_class_name = user32.GetClassNameW
    get_window_text = user32.GetWindowTextW
    get_dlg_ctrl_id = user32.GetDlgCtrlID

    def _class_of(handle: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        get_class_name(int(handle), buf, 255)
        return str(buf.value or "")

    def _text_of(handle: int) -> str:
        buf = ctypes.create_unicode_buffer(512)
        get_window_text(int(handle), buf, 511)
        return str(buf.value or "")

    rows: List[Dict[str, Any]] = []
    enum_child_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(chwnd, _lparam):
        handle = int(chwnd)
        rows.append(
            {
                "handle": handle,
                "class_name": _class_of(handle),
                "text": _text_of(handle),
                "ctrl_id": int(get_dlg_ctrl_id(handle)),
            }
        )
        return True

    user32.EnumChildWindows(hwnd, enum_child_proc(_cb), 0)
    return rows


def _find_save_button_win32(win32_rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    # Prefer explicit save buttons first.
    for row in win32_rows:
        text = str(row.get("text", "") or "").strip().lower()
        class_name = str(row.get("class_name", "") or "")
        if re.search(r"(save|speicher)", text) and re.search(r"(bitbtn|button)", class_name, re.IGNORECASE):
            return row
    for row in win32_rows:
        text = str(row.get("text", "") or "").strip().lower()
        class_name = str(row.get("class_name", "") or "")
        if re.search(r"(save|speicher)", text):
            return row
        if re.search(r"(button|bitbtn|toolbutton|rzbutton|speedbutton)", class_name, re.IGNORECASE) and text:
            return row
    return None


def _close_dialog(dialog: Any) -> Dict[str, Any]:
    for caption in ("Cancel", "Abbrechen", "Close", "Schließen", "No", "Nein", "OK", "Ok"):
        try:
            btn = dialog.child_window(title=caption, control_type="Button")
            if btn.exists(timeout=0.2):
                try:
                    btn.invoke()
                    return {"status": "ok", "method": "invoke", "caption": caption}
                except Exception:
                    btn.click()
                    return {"status": "ok", "method": "click", "caption": caption}
        except Exception:
            continue
    try:
        dialog.type_keys("{ESC}")
        return {"status": "ok", "method": "esc"}
    except Exception as exc:
        return {"status": "error", "error": repr(exc)}


def _close_vacs_without_saving(pid: int) -> Dict[str, Any]:
    main = _find_vacs_main_window(pid)
    if main is None:
        return {"status": "already_closed"}
    try:
        main.close()
    except Exception as exc:
        return {"status": "close_error", "error": repr(exc)}

    deadline = time.perf_counter() + 10.0
    save_actions: List[Dict[str, Any]] = []
    while time.perf_counter() < deadline:
        dialogs = _find_dialog_candidates(pid, main_handle=int(_signature(main).get("handle", 0) or 0))
        for d in dialogs:
            sig = _signature(d)
            title = str(sig.get("title", ""))
            if re.search(r"(save|speicher|warning|confirm)", title, re.IGNORECASE):
                action = _close_dialog_with_no_preference(d)
                save_actions.append({"dialog": sig, "action": action})
        if not _collect_windows_for_pid(pid):
            return {"status": "closed", "save_actions": save_actions}
        time.sleep(0.2)
    return {"status": "close_timeout", "save_actions": save_actions}


def _close_dialog_with_no_preference(dialog: Any) -> Dict[str, Any]:
    # Prefer explicit "No" to avoid save side effects.
    for caption in ("No", "Nein", "Don't Save", "Nicht speichern"):
        try:
            btn = dialog.child_window(title=caption, control_type="Button")
            if btn.exists(timeout=0.2):
                try:
                    btn.invoke()
                    return {"status": "ok", "method": "invoke", "caption": caption}
                except Exception:
                    btn.click()
                    return {"status": "ok", "method": "click", "caption": caption}
        except Exception:
            continue
    # fallback by common automation id for "No"
    try:
        btn = dialog.child_window(auto_id="CommandButton_7", control_type="Button")
        if btn.exists(timeout=0.2):
            try:
                btn.invoke()
                return {"status": "ok", "method": "invoke_autoid", "caption": "CommandButton_7"}
            except Exception:
                btn.click()
                return {"status": "ok", "method": "click_autoid", "caption": "CommandButton_7"}
    except Exception:
        pass
    return _close_dialog(dialog)


def _run_interim_reimport(args: argparse.Namespace) -> Dict[str, Any]:
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
    cp = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stdout = str(cp.stdout or "").strip()
    payload: Dict[str, Any] = {
        "returncode": int(cp.returncode),
        "stderr": str(cp.stderr or "").strip(),
        "stdout_tail": stdout[-5000:],
    }
    try:
        parsed = json.loads(stdout) if stdout else {}
        payload["parsed"] = parsed
    except Exception:
        payload["parsed"] = {}
    return payload


def _start_vacs(vacs_exe: str) -> Dict[str, Any]:
    proc = subprocess.Popen([str(vacs_exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    return {"pid": int(proc.pid), "exe": str(vacs_exe)}


def _kill_vacs_processes() -> None:
    for image in ("VACSVIEWER_32.exe", "vacsviewer.exe"):
        subprocess.run(["taskkill", "/IM", image, "/T", "/F"], capture_output=True, text=True, check=False)


def _kill_vacs_pid(pid: int) -> None:
    if int(pid or 0) <= 0:
        return
    subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, text=True, check=False)


def _pick_target_child(main: Any, target_class: str) -> Optional[Any]:
    children = _collect_child_windows(main)
    for c in children:
        sig = _signature(c)
        if sig.get("class_name") == target_class:
            return c
    return children[0] if children else None


def run_round(args: argparse.Namespace, recipe: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "round_id": recipe["round_id"],
        "note": recipe.get("note", ""),
        "started_at": _now_iso(),
        "steps": [],
    }

    def step(name: str, **payload: Any) -> None:
        row["steps"].append({"time": _now_iso(), "step": name, "payload": payload})

    _kill_vacs_processes()
    start_info = _start_vacs(str(args.vacs_exe))
    step("start_vacs", **start_info)

    interim = _run_interim_reimport(args)
    step("interim_reimport", **interim)
    parsed = dict(interim.get("parsed") or {})
    if not bool(parsed.get("ok")):
        _kill_vacs_pid(int(parsed.get("vacs_pid", 0) or start_info.get("pid", 0) or 0))
        row["ok"] = False
        row["error"] = "interim_reimport_failed"
        row["finished_at"] = _now_iso()
        return row

    vacs_pid = int(parsed.get("vacs_pid", 0) or 0)
    row["vacs_pid"] = vacs_pid
    main = _find_vacs_main_window(vacs_pid)
    if main is None:
        row["ok"] = False
        row["error"] = "vacs_main_window_missing"
        row["finished_at"] = _now_iso()
        return row

    main_sig = _signature(main)
    main_handle = int(main_sig.get("handle", 0) or 0)
    step("vacs_main", signature=main_sig)

    children = [_signature(c) for c in _collect_child_windows(main)]
    step("child_windows_snapshot", count=len(children), children=children)

    target = _pick_target_child(main, str(recipe.get("target_class", "")))
    if target is None:
        row["ok"] = False
        row["error"] = "no_child_window_available"
        row["finished_at"] = _now_iso()
        return row
    target_sig = _signature(target)
    step("target_selected", target=target_sig)

    # Activate target child and optionally execute pitfall pre-action.
    try:
        w32_target = Desktop(backend="win32").window(handle=int(target_sig.get("handle", 0)))
        w32_target.set_focus()
    except Exception as exc:
        row["ok"] = False
        row["error"] = f"target_focus_failed: {exc!r}"
        row["finished_at"] = _now_iso()
        return row

    pre_action_key = recipe.get("pre_action_key")
    pitfall_windows: List[Dict[str, Any]] = []
    if pre_action_key:
        try:
            w32_target.type_keys(str(pre_action_key), set_foreground=True)
            time.sleep(0.3)
            dialogs = _find_dialog_candidates(vacs_pid, main_handle=main_handle)
            for d in dialogs:
                dsig = _signature(d)
                controls = _dialog_controls(d, limit=80)
                close_action = _close_dialog(d)
                pitfall_windows.append(
                    {"signature": dsig, "controls": controls, "close_action": close_action}
                )
            step("pre_action_probe", key=str(pre_action_key), dialog_count=len(pitfall_windows), dialogs=pitfall_windows)
        except Exception as exc:
            step("pre_action_probe_failed", key=str(pre_action_key), error=repr(exc))

    # Re-focus target window after optional pre-action cleanup.
    try:
        w32_target = Desktop(backend="win32").window(handle=int(target_sig.get("handle", 0)))
        w32_target.set_focus()
    except Exception:
        pass

    # Open export dialog with deterministic trigger ladder.
    export_trigger_attempts: List[Dict[str, Any]] = []
    w32_main = Desktop(backend="win32").window(handle=main_handle)
    try:
        w32_target.type_keys("{F7}", set_foreground=True)
        export_trigger_attempts.append({"method": "target_f7", "status": "ok"})
    except Exception as exc:
        export_trigger_attempts.append({"method": "target_f7", "status": "error", "error": repr(exc)})
    try:
        w32_main.type_keys("{F7}", set_foreground=True)
        export_trigger_attempts.append({"method": "main_f7", "status": "ok"})
    except Exception as exc:
        export_trigger_attempts.append({"method": "main_f7", "status": "error", "error": repr(exc)})
    try:
        ctypes.windll.user32.SendMessageW(int(main_handle), WM_COMMAND, int(VACS_EXPORT_COMMAND_ID), 0)
        export_trigger_attempts.append({"method": "main_wm_command_52", "status": "ok"})
    except Exception as exc:
        export_trigger_attempts.append({"method": "main_wm_command_52", "status": "error", "error": repr(exc)})
    try:
        w32_main.menu_select("IO->Export data...")
        export_trigger_attempts.append({"method": "menu_select_io_export_data", "status": "ok"})
    except Exception as exc:
        export_trigger_attempts.append({"method": "menu_select_io_export_data", "status": "error", "error": repr(exc)})
    step("export_triggered", attempts=export_trigger_attempts)

    deadline = time.perf_counter() + float(args.dialog_timeout_s)
    found_export = None
    wrong_windows: List[Dict[str, Any]] = []
    seen_wrong_handles: set[int] = set()
    while time.perf_counter() < deadline:
        exp = _find_data_export_dialog(vacs_pid, main_handle=main_handle)
        if exp is not None:
            found_export = exp
            break
        for d in _find_dialog_candidates(vacs_pid, main_handle=main_handle):
            dsig = _signature(d)
            handle = int(dsig.get("handle", 0) or 0)
            title = str(dsig.get("title", ""))
            if handle > 0 and handle not in seen_wrong_handles and not re.search(r"data\s*export", title, re.IGNORECASE):
                seen_wrong_handles.add(handle)
                wrong_windows.append(dsig)
        time.sleep(0.2)

    if found_export is None:
        step("export_dialog_missing", wrong_windows=wrong_windows)
        # Cleanup before round exit.
        for d in _find_dialog_candidates(vacs_pid, main_handle=main_handle):
            _close_dialog(d)
        close_result = _close_vacs_without_saving(vacs_pid)
        step("close_vacs", result=close_result)
        if str(close_result.get("status")) != "closed":
            _kill_vacs_pid(vacs_pid)
            step("close_vacs_force_kill", pid=vacs_pid)
        row["ok"] = False
        row["error"] = "data_export_dialog_not_found"
        row["finished_at"] = _now_iso()
        return row

    export_sig = _signature(found_export)
    controls = _dialog_controls(found_export, limit=180)
    save_btn = _find_save_button(controls)
    win32_rows = _win32_child_dump(int(export_sig.get("handle", 0) or 0))
    save_btn_win32 = _find_save_button_win32(win32_rows)
    try:
        dialog_visible = bool(found_export.is_visible())
    except Exception:
        dialog_visible = False
    try:
        dialog_enabled = bool(found_export.is_enabled())
    except Exception:
        dialog_enabled = None
    close_action = _close_dialog(found_export)
    step(
        "data_export_dialog_observed",
        dialog=export_sig,
        dialog_visible=dialog_visible,
        dialog_enabled=dialog_enabled,
        controls_count=len(controls),
        controls=controls,
        save_button=save_btn,
        win32_children=win32_rows,
        save_button_win32=save_btn_win32,
        close_action=close_action,
        wrong_windows=wrong_windows,
    )

    # Close remaining stray dialogs.
    residual: List[Dict[str, Any]] = []
    for d in _find_dialog_candidates(vacs_pid, main_handle=main_handle):
        dsig = _signature(d)
        action = _close_dialog(d)
        residual.append({"dialog": dsig, "close_action": action})
    step("residual_dialog_cleanup", count=len(residual), rows=residual)

    close_result = _close_vacs_without_saving(vacs_pid)
    step("close_vacs", result=close_result)
    if str(close_result.get("status")) != "closed":
        _kill_vacs_pid(vacs_pid)
        step("close_vacs_force_kill", pid=vacs_pid)

    row["ok"] = True
    row["save_button_found"] = bool(save_btn is not None)
    row["finished_at"] = _now_iso()
    return row


def run_all(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": _now_iso(),
        "args": {
            "akabak_exe": str(args.akabak_exe),
            "vacs_exe": str(args.vacs_exe),
            "dialog_timeout_s": float(args.dialog_timeout_s),
        },
        "rounds": [],
    }

    for recipe in ROUND_RECIPES:
        row = run_round(args, recipe, run_dir)
        summary["rounds"].append(row)
        per_round_file = run_dir / f"{recipe['round_id']}.json"
        per_round_file.write_text(json.dumps(row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    ok_count = sum(1 for r in summary["rounds"] if bool(r.get("ok")))
    save_found_count = sum(1 for r in summary["rounds"] if bool(r.get("save_button_found")))
    summary["ok_rounds"] = ok_count
    summary["save_button_found_rounds"] = save_found_count
    summary["finished_at"] = _now_iso()

    summary_file = run_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary["summary_file"] = str(summary_file)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run 5 rounds of VACS Data Export dialog discovery.")
    parser.add_argument("--akabak-exe", required=True, help="Path to AKABAK.exe")
    parser.add_argument("--vacs-exe", required=True, help="Path to VACS viewer exe")
    parser.add_argument("--output-dir", default="runner_test_workspace/logs/vacs_export_rounds", help="Log output root")
    parser.add_argument("--dialog-timeout-s", type=float, default=5.0, help="Wait timeout for Data Export dialog")
    parser.add_argument("--interim-timeout-s", type=int, default=90, help="Interim script global timeout")
    parser.add_argument("--interim-idle-timeout-s", type=int, default=20, help="Interim script idle timeout")
    parser.add_argument("--interim-startup-timeout-s", type=int, default=25, help="Interim script startup timeout")
    return parser


def main() -> int:
    from app.audit_mode import enable_audit_mode

    enable_audit_mode(entrypoint="scripts.vacs_export_dialog_rounds.main")
    args = build_parser().parse_args()
    summary = run_all(args)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if int(summary.get("ok_rounds", 0)) == len(ROUND_RECIPES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
