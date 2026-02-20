"""External tool runner wrappers for ATH, AKABAK and VACS."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import os
import subprocess
from typing import Iterable, List, Optional, Sequence


_ATH_DIM_RE = re.compile(
    r"(?i)\b(length|width|height)\b[^0-9\-+]*([-+]?\d+(?:[.,]\d+)?)"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class RunnerResult:
    tool: str
    command: List[str]
    started_at: str
    finished_at: str
    attempts: int
    exit_code: int
    timed_out: bool
    stdout_log: str
    stderr_log: str
    summary_log: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class AthDimensions:
    horn_length_mm: Optional[float]
    horn_width_mm: Optional[float]
    horn_height_mm: Optional[float]
    raw_line: str


def parse_ath_dimensions(stdout_text: str) -> AthDimensions:
    length = None
    width = None
    height = None
    raw = ""
    for line in stdout_text.splitlines():
        line_matches = list(_ATH_DIM_RE.finditer(line))
        if not line_matches:
            continue
        lowered = line.lower()
        if not ("length" in lowered and "width" in lowered and "height" in lowered):
            continue
        raw = line.strip()
        for match in line_matches:
            label = match.group(1).lower()
            try:
                value = float(match.group(2).replace(",", "."))
            except ValueError:
                continue
            if label == "length":
                length = value
            elif label == "width":
                width = value
            elif label == "height":
                height = value
        break
    return AthDimensions(
        horn_length_mm=length,
        horn_width_mm=width,
        horn_height_mm=height,
        raw_line=raw,
    )


class _SubprocessRunner:
    def __init__(
        self,
        tool_name: str,
        executable: str | Path,
        *,
        default_timeout_s: int = 300,
        default_retries: int = 1,
        base_args: Sequence[str] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.executable = str(executable)
        self.default_timeout_s = max(1, int(default_timeout_s))
        self.default_retries = max(1, int(default_retries))
        self.base_args = list(base_args or [])

    def run(
        self,
        args: Iterable[str],
        *,
        version_logs_dir: str | Path,
        workdir: str | Path | None = None,
        timeout_s: int | None = None,
        retries: int | None = None,
        log_prefix: str | None = None,
    ) -> RunnerResult:
        command = [self.executable, *self.base_args, *list(args)]
        logs_dir = Path(version_logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        prefix = log_prefix or self.tool_name.lower()

        stdout_log = logs_dir / f"{prefix}.stdout.log"
        stderr_log = logs_dir / f"{prefix}.stderr.log"
        summary_log = logs_dir / f"{prefix}.runner.log"
        stdout_log.write_text("", encoding="utf-8")
        stderr_log.write_text("", encoding="utf-8")
        summary_log.write_text("", encoding="utf-8")

        effective_timeout = timeout_s if timeout_s is not None else self.default_timeout_s
        effective_retries = retries if retries is not None else self.default_retries
        effective_retries = max(1, int(effective_retries))

        started_at = _now_iso()
        last_exit_code = -1
        timed_out = False

        for attempt in range(1, effective_retries + 1):
            attempt_started = _now_iso()
            try:
                popen_kwargs = {
                    "cwd": str(workdir) if workdir else None,
                    "timeout": effective_timeout,
                    "capture_output": True,
                    "text": True,
                    "encoding": "utf-8",
                    "errors": "replace",
                    "check": False,
                }
                proc = _run_with_process_tree_timeout(
                    command,
                    **popen_kwargs,
                )
                last_exit_code = int(proc.returncode)
                stdout_log.write_text(
                    stdout_log.read_text(encoding="utf-8")
                    + f"\n### attempt {attempt} @ {attempt_started}\n{proc.stdout}",
                    encoding="utf-8",
                )
                stderr_log.write_text(
                    stderr_log.read_text(encoding="utf-8")
                    + f"\n### attempt {attempt} @ {attempt_started}\n{proc.stderr}",
                    encoding="utf-8",
                )
                summary_log.write_text(
                    summary_log.read_text(encoding="utf-8")
                    + f"[{attempt_started}] attempt={attempt} exit_code={last_exit_code}\n",
                    encoding="utf-8",
                )
                if last_exit_code == 0:
                    break
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                timeout_stdout = exc.stdout or ""
                timeout_stderr = exc.stderr or ""
                stdout_log.write_text(
                    stdout_log.read_text(encoding="utf-8")
                    + f"\n### attempt {attempt} @ {attempt_started} (timeout)\n{timeout_stdout}",
                    encoding="utf-8",
                )
                stderr_log.write_text(
                    stderr_log.read_text(encoding="utf-8")
                    + f"\n### attempt {attempt} @ {attempt_started} (timeout)\n{timeout_stderr}",
                    encoding="utf-8",
                )
                summary_log.write_text(
                    summary_log.read_text(encoding="utf-8")
                    + f"[{attempt_started}] attempt={attempt} timeout after {effective_timeout}s\n",
                    encoding="utf-8",
                )
                last_exit_code = -1

        finished_at = _now_iso()
        return RunnerResult(
            tool=self.tool_name,
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            attempts=effective_retries,
            exit_code=last_exit_code,
            timed_out=timed_out,
            stdout_log=str(stdout_log),
            stderr_log=str(stderr_log),
            summary_log=str(summary_log),
        )


def _run_with_process_tree_timeout(
    command: Sequence[str],
    *,
    cwd: str | None,
    timeout: int,
    capture_output: bool,
    text: bool,
    encoding: str,
    errors: str,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    if not capture_output:
        raise ValueError("capture_output must be True for runner logging")
    popen_kwargs = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": text,
        "encoding": encoding,
        "errors": errors,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(list(command), **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(proc.pid)
        timeout_stdout = ""
        timeout_stderr = ""
        try:
            timeout_stdout, timeout_stderr = proc.communicate(timeout=5)
        except Exception:
            timeout_stdout = str(exc.stdout or "")
            timeout_stderr = str(exc.stderr or "")
        raise subprocess.TimeoutExpired(
            cmd=list(command),
            timeout=timeout,
            output=timeout_stdout,
            stderr=timeout_stderr,
        )

    completed = subprocess.CompletedProcess(
        args=list(command),
        returncode=int(proc.returncode or 0),
        stdout=str(stdout or ""),
        stderr=str(stderr or ""),
    )
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            returncode=completed.returncode,
            cmd=completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def _terminate_process_tree(pid: int) -> None:
    if int(pid) <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        return
    try:
        os.killpg(int(pid), 9)
    except Exception:
        try:
            os.kill(int(pid), 9)
        except Exception:
            pass


class AthRunner(_SubprocessRunner):
    def __init__(self, executable: str | Path, *, base_args: Sequence[str] | None = None) -> None:
        super().__init__(
            "ATH",
            executable,
            default_timeout_s=180,
            default_retries=2,
            base_args=base_args,
        )

    def run_cfg(
        self,
        cfg_path: str | Path,
        *,
        version_logs_dir: str | Path,
        workdir: str | Path | None = None,
    ) -> RunnerResult:
        return self.run(
            [str(cfg_path)],
            version_logs_dir=version_logs_dir,
            workdir=workdir,
            log_prefix="ath",
        )


class AkabakRunner(_SubprocessRunner):
    def __init__(self, executable: str | Path, *, base_args: Sequence[str] | None = None) -> None:
        super().__init__(
            "AKABAK",
            executable,
            default_timeout_s=600,
            default_retries=2,
            base_args=base_args,
        )

    def run_project(
        self,
        abec_project: str | Path,
        *,
        version_logs_dir: str | Path,
        workdir: str | Path | None = None,
    ) -> RunnerResult:
        return self.run(
            [str(abec_project)],
            version_logs_dir=version_logs_dir,
            workdir=workdir,
            log_prefix="akabak",
        )


class VacsRunner(_SubprocessRunner):
    def __init__(self, executable: str | Path, *, base_args: Sequence[str] | None = None) -> None:
        super().__init__(
            "VACS",
            executable,
            default_timeout_s=600,
            default_retries=2,
            base_args=base_args,
        )

    def run_export(
        self,
        project_or_abec: str | Path,
        *,
        version_logs_dir: str | Path,
        workdir: str | Path | None = None,
    ) -> RunnerResult:
        return self.run(
            [str(project_or_abec)],
            version_logs_dir=version_logs_dir,
            workdir=workdir,
            log_prefix="vacs",
        )
