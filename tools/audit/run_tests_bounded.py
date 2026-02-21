#!/usr/bin/env python3
"""Bounded unittest runner for audit use.

Behavior:
- discovers tests via unittest loader (no unbounded discover run command)
- filters by default skip tokens (UI/Qt/GUI/preview/stl/gmsh), with opt-in include overrides
- runs tests in chunks with hard timeout
- bisects timed-out chunks to isolate hanging tests
- writes audit artifacts:
  - audit/tests_discovered.txt
  - audit/tests_summary.md
  - audit/flaky_or_hanging_tests.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import subprocess
import sys
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


DEFAULT_SKIP_TOKENS = ["ui", "qt", "gui", "preview", "stl", "gmsh"]
SUMMARY_FAILING_TESTS_TOP_N = 20
INCLUDE_CHOICES = ("ui", "preview", "external")
INCLUDE_TOKEN_OVERRIDES: dict[str, set[str]] = {
    "ui": {"ui", "qt", "gui"},
    "preview": {"preview", "stl"},
    "external": {"gmsh"},
}


@dataclass
class ChunkResult:
    run_id: int
    depth: int
    kind: str
    tests: list[str]
    timeout: bool
    returncode: int | None
    duration_s: float
    log_path: str
    ran: int | None = None
    failures: int | None = None
    errors: int | None = None
    skipped: int | None = None
    failed_test_ids: list[str] = field(default_factory=list)
    error_test_ids: list[str] = field(default_factory=list)


def _iter_suite_tests(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_suite_tests(item)
        else:
            yield item


def discover_test_ids(
    start_dir: str, pattern: str, top_level_dir: str | None
) -> tuple[list[str], list[str]]:
    loader = unittest.defaultTestLoader
    suite = loader.discover(start_dir=start_dir, pattern=pattern, top_level_dir=top_level_dir)
    ids: list[str] = []
    for test in _iter_suite_tests(suite):
        if hasattr(test, "id"):
            ids.append(test.id())
    # Deduplicate while keeping deterministic order.
    unique_ids = sorted(set(ids))
    discover_errors = [str(err) for err in getattr(loader, "errors", [])]
    return unique_ids, discover_errors


def chunked(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def classify_skip(test_id: str, skip_tokens: list[str], skip_regex: str | None) -> str | None:
    lowered = test_id.lower()
    for token in skip_tokens:
        if token.lower() in lowered:
            return f"token:{token}"
    if skip_regex and re.search(skip_regex, test_id, flags=re.IGNORECASE):
        return f"regex:{skip_regex}"
    return None


def resolve_include_modes(include_values: list[str]) -> list[str]:
    # Preserve CLI order while removing duplicates.
    return list(dict.fromkeys(include_values))


def resolve_effective_skip_tokens(skip_tokens: list[str], include_modes: list[str]) -> list[str]:
    excluded = {token.lower() for token in skip_tokens}
    for mode in include_modes:
        excluded -= INCLUDE_TOKEN_OVERRIDES.get(mode, set())
    return [token for token in skip_tokens if token.lower() in excluded]


def resolve_effective_timeout_s(timeout_s: float, include_modes: list[str], include_strict_timeout_s: float) -> float:
    if any(mode in {"ui", "preview"} for mode in include_modes):
        return min(timeout_s, include_strict_timeout_s)
    return timeout_s


def parse_unittest_output(output: str) -> dict[str, object]:
    ran_match = re.search(r"Ran (\d+) tests? in", output)
    ran = int(ran_match.group(1)) if ran_match else None

    failed_test_ids: list[str] = []
    error_test_ids: list[str] = []
    for line in output.splitlines():
        m = re.match(r"^(FAIL|ERROR):\s+(.+)$", line.strip())
        if not m:
            continue
        kind = m.group(1)
        raw = m.group(2)
        # Keep unittest's canonical test id if present in parentheses.
        paren = re.search(r"\(([^)]+)\)\s*$", raw)
        test_id = paren.group(1) if paren else raw
        if kind == "FAIL":
            failed_test_ids.append(test_id)
        else:
            error_test_ids.append(test_id)

    failures = None
    errors = None
    skipped = None
    failed_summary = re.search(r"FAILED \(([^)]+)\)", output)
    if failed_summary:
        parts = failed_summary.group(1).split(",")
        for part in parts:
            key, _, value = part.strip().partition("=")
            key = key.strip()
            value = value.strip()
            if key == "failures":
                failures = int(value)
            elif key == "errors":
                errors = int(value)
            elif key == "skipped":
                skipped = int(value)
    else:
        ok_summary = re.search(r"OK(?: \(([^)]+)\))?$", output, flags=re.MULTILINE)
        if ok_summary and ok_summary.group(1):
            for part in ok_summary.group(1).split(","):
                key, _, value = part.strip().partition("=")
                if key.strip() == "skipped":
                    skipped = int(value.strip())
        failures = failures if failures is not None else 0
        errors = errors if errors is not None else 0

    return {
        "ran": ran,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "failed_test_ids": failed_test_ids,
        "error_test_ids": error_test_ids,
    }


def collect_failure_error_stats(
    results: list[ChunkResult],
) -> tuple[int, int, list[str], list[str], list[str]]:
    observed_failures_total = sum(int(item.failures or 0) for item in results)
    observed_errors_total = sum(int(item.errors or 0) for item in results)
    failing_test_ids = sorted(set(sum((item.failed_test_ids for item in results), [])))
    error_test_ids = sorted(set(sum((item.error_test_ids for item in results), [])))
    merged: list[str] = []
    seen: set[str] = set()
    for test_id in [*failing_test_ids, *error_test_ids]:
        token = str(test_id)
        if token in seen:
            continue
        seen.add(token)
        merged.append(token)
    top_failing_names = merged[:SUMMARY_FAILING_TESTS_TOP_N]
    return (
        observed_failures_total,
        observed_errors_total,
        failing_test_ids,
        error_test_ids,
        top_failing_names,
    )


def write_chunk_log(log_dir: Path, run_id: int, label: str, output: str) -> str:
    log_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{run_id:04d}_{label}.log"
    path = log_dir / file_name
    path.write_text(output, encoding="utf-8")
    return str(path.as_posix())


class BoundedRunner:
    def __init__(
        self,
        python_exe: str,
        timeout_s: float,
        cwd: Path,
        logs_dir: Path,
    ) -> None:
        self.python_exe = python_exe
        self.timeout_s = timeout_s
        self.cwd = cwd
        self.logs_dir = logs_dir
        self.run_counter = 0
        self.results: list[ChunkResult] = []

    @staticmethod
    def _kill_process_tree(pid: int) -> None:
        if platform.system().lower().startswith("win"):
            # /T terminates the full child tree, /F forces termination.
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
            return
        try:
            os.killpg(pid, 9)
        except Exception:
            pass

    def _run_once(self, tests: list[str], depth: int, kind: str) -> ChunkResult:
        self.run_counter += 1
        run_id = self.run_counter
        label = f"d{depth}_{kind}_{len(tests)}tests"
        cmd = [self.python_exe, "-m", "unittest", *tests]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        tests_dir = self.cwd / "tests"
        path_entries = [str(self.cwd)]
        if tests_dir.exists():
            path_entries.insert(0, str(tests_dir))
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join(path_entries + ([existing_pythonpath] if existing_pythonpath else []))
        started = time.monotonic()
        timeout = False
        returncode: int | None = None
        output = ""

        popen_kwargs = {
            "cwd": str(self.cwd),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if platform.system().lower().startswith("win"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        proc = subprocess.Popen(cmd, **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout_s)
            returncode = proc.returncode
            output = (stdout or "") + ("\n" if stdout and stderr else "") + (stderr or "")
        except subprocess.TimeoutExpired:
            timeout = True
            self._kill_process_tree(proc.pid)
            stdout, stderr = proc.communicate()
            output = (stdout or "") + ("\n" if stdout and stderr else "") + (stderr or "")
            output += f"\n[bounded-runner] TIMEOUT after {self.timeout_s:.1f}s (process tree terminated)\n"

        duration_s = time.monotonic() - started
        metrics = parse_unittest_output(output)
        log_path = write_chunk_log(self.logs_dir, run_id, label, output)
        result = ChunkResult(
            run_id=run_id,
            depth=depth,
            kind=kind,
            tests=list(tests),
            timeout=timeout,
            returncode=returncode,
            duration_s=duration_s,
            log_path=log_path,
            ran=metrics["ran"],
            failures=metrics["failures"],
            errors=metrics["errors"],
            skipped=metrics["skipped"],
            failed_test_ids=metrics["failed_test_ids"],
            error_test_ids=metrics["error_test_ids"],
        )
        self.results.append(result)
        return result

    def run_chunk_with_bisect(self, tests: list[str], depth: int = 0, kind: str = "chunk") -> None:
        if not tests:
            return
        result = self._run_once(tests=tests, depth=depth, kind=kind)
        if not result.timeout or len(tests) <= 1:
            return
        midpoint = len(tests) // 2
        left = tests[:midpoint]
        right = tests[midpoint:]
        self.run_chunk_with_bisect(left, depth=depth + 1, kind="bisect_left")
        self.run_chunk_with_bisect(right, depth=depth + 1, kind="bisect_right")


def render_discovered_file(
    out_path: Path,
    discovered: list[str],
    selected: list[str],
    skipped: list[tuple[str, str]],
    discover_errors: list[str],
) -> None:
    lines: list[str] = []
    lines.append(f"# Discovered Tests ({dt.datetime.now().isoformat()})")
    lines.append("")
    lines.append(f"- discovered_total: {len(discovered)}")
    lines.append(f"- selected_total: {len(selected)}")
    lines.append(f"- skipped_default_total: {len(skipped)}")
    lines.append(f"- discover_errors: {len(discover_errors)}")
    lines.append("")
    if discover_errors:
        lines.append("## Discover Errors")
        for err in discover_errors:
            lines.append(f"- {err}")
        lines.append("")
    lines.append("## Selected Tests")
    for test_id in selected:
        lines.append(test_id)
    lines.append("")
    lines.append("## Skipped By Default Filter")
    for test_id, reason in skipped:
        lines.append(f"{test_id}  # {reason}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_summary_file(
    out_path: Path,
    discovered_count: int,
    selected_count: int,
    skipped_count: int,
    discover_errors: list[str],
    results: list[ChunkResult],
    args: argparse.Namespace,
    include_modes: list[str],
    effective_skip_tokens: list[str],
    effective_timeout_s: float,
) -> None:
    timeouts = [r for r in results if r.timeout]
    failed_chunks = [r for r in results if not r.timeout and (r.returncode or 0) != 0]
    passed_chunks = [r for r in results if not r.timeout and (r.returncode or 0) == 0]
    total_duration = sum(r.duration_s for r in results)
    ran_total = sum(r.ran or 0 for r in results)
    (
        observed_failures_total,
        observed_errors_total,
        failing_test_ids,
        error_test_ids,
        top_failing_names,
    ) = collect_failure_error_stats(results)

    lines: list[str] = []
    lines.append("# Bounded Test Summary")
    lines.append("")
    lines.append("## Configuration")
    include_arg = " ".join(f"--include {mode}" for mode in include_modes)
    command_suffix = f" {include_arg}" if include_arg else ""
    lines.append(
        f"- command: `python tools/audit/run_tests_bounded.py --chunk-size {args.chunk_size} --timeout-s {args.timeout_s}{command_suffix}`"
    )
    lines.append(f"- started_utc: {dt.datetime.now(dt.UTC).isoformat()}")
    lines.append(f"- chunk_size: {args.chunk_size}")
    lines.append(f"- configured_timeout_s: {args.timeout_s}")
    lines.append(f"- effective_timeout_s: {effective_timeout_s}")
    lines.append(
        f"- include_modes: `{', '.join(include_modes)}`"
        if include_modes
        else "- include_modes: `none`"
    )
    lines.append(f"- configured_skip_tokens: `{', '.join(args.skip_token)}`")
    lines.append(f"- effective_skip_tokens: `{', '.join(effective_skip_tokens)}`")
    lines.append("")
    lines.append("## Discovery")
    lines.append(f"- discovered_total: {discovered_count}")
    lines.append(f"- selected_total: {selected_count}")
    lines.append(f"- skipped_default_total: {skipped_count}")
    lines.append(f"- discover_errors: {len(discover_errors)}")
    lines.append("")
    lines.append("## Execution")
    lines.append(f"- subprocess_runs_total: {len(results)}")
    lines.append(f"- passed_runs: {len(passed_chunks)}")
    lines.append(f"- failed_runs: {len(failed_chunks)}")
    lines.append(f"- timeout_runs: {len(timeouts)}")
    lines.append(f"- observed_ran_total_across_runs: {ran_total}")
    lines.append(f"- accumulated_duration_s: {total_duration:.2f}")
    lines.append(f"- observed_failures_total: {observed_failures_total}")
    lines.append(f"- observed_errors_total: {observed_errors_total}")
    lines.append("")
    lines.append(f"## Failing Test Names (Top {SUMMARY_FAILING_TESTS_TOP_N})")
    if top_failing_names:
        for test_id in top_failing_names:
            lines.append(f"- `{test_id}`")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Failing/Errored Breakdown")
    lines.append(f"- failed_test_names: {len(failing_test_ids)}")
    lines.append(f"- error_test_names: {len(error_test_ids)}")
    lines.append("")
    lines.append("## Timeout Runs")
    if timeouts:
        for item in timeouts:
            lines.append(
                f"- run {item.run_id}: tests={len(item.tests)}, depth={item.depth}, kind={item.kind}, log=`{item.log_path}`"
            )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Run Logs")
    for item in results:
        lines.append(
            f"- run {item.run_id}: rc={item.returncode}, timeout={item.timeout}, tests={len(item.tests)}, duration_s={item.duration_s:.2f}, log=`{item.log_path}`"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_flaky_hanging_file(out_path: Path, results: list[ChunkResult]) -> None:
    single_timeouts = [r for r in results if r.timeout and len(r.tests) == 1]
    unresolved_group_timeouts = [r for r in results if r.timeout and len(r.tests) > 1]

    # Flaky detection from a single run is intentionally conservative:
    # mark as "not assessed" unless the same single test appears with mixed outcomes.
    per_single: dict[str, set[str]] = {}
    for r in results:
        if len(r.tests) != 1:
            continue
        test_id = r.tests[0]
        state = "timeout" if r.timeout else ("pass" if (r.returncode or 0) == 0 else "fail")
        per_single.setdefault(test_id, set()).add(state)
    flaky = sorted(t for t, states in per_single.items() if len(states) > 1)

    lines: list[str] = []
    lines.append("# Flaky Or Hanging Tests")
    lines.append("")
    lines.append("## Hanging Candidates (Timeout at Single-Test Granularity)")
    if single_timeouts:
        for r in single_timeouts:
            lines.append(f"- `{r.tests[0]}` (timeout={r.duration_s:.2f}s, log=`{r.log_path}`)")
    else:
        lines.append("- none detected")
    lines.append("")
    lines.append("## Unresolved Timeout Groups")
    if unresolved_group_timeouts:
        for r in unresolved_group_timeouts:
            sample = ", ".join(r.tests[:3])
            suffix = " ..." if len(r.tests) > 3 else ""
            lines.append(f"- {len(r.tests)} tests (depth={r.depth}, kind={r.kind}): `{sample}{suffix}`; log=`{r.log_path}`")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Flaky Candidates")
    if flaky:
        for test_id in flaky:
            lines.append(f"- `{test_id}`")
    else:
        lines.append("- none observed in this single bounded pass")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded unittest discovery/runner for audit.")
    parser.add_argument("--start-dir", default="tests")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--top-level-dir", default=None)
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        choices=list(INCLUDE_CHOICES),
        help="Include additional test families that are skipped by default.",
    )
    parser.add_argument(
        "--include-strict-timeout-s",
        type=float,
        default=60.0,
        help="When include ui/preview is used, cap per-chunk timeout to this value.",
    )
    parser.add_argument("--audit-dir", default="audit")
    parser.add_argument("--skip-token", action="append", default=list(DEFAULT_SKIP_TOKENS))
    parser.add_argument("--skip-regex", default=None)
    parser.add_argument("--max-tests", type=int, default=None)
    parser.add_argument("--python-exe", default=sys.executable)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.chunk_size <= 0:
        raise SystemExit("--chunk-size must be > 0")
    if args.timeout_s <= 0:
        raise SystemExit("--timeout-s must be > 0")
    if args.include_strict_timeout_s <= 0:
        raise SystemExit("--include-strict-timeout-s must be > 0")

    repo_root = Path(__file__).resolve().parents[2]
    audit_dir = (repo_root / args.audit_dir).resolve()
    audit_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = audit_dir / "data" / "bounded_chunk_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    start_dir = str((repo_root / args.start_dir).resolve())
    top_level_dir = (
        str((repo_root / args.top_level_dir).resolve())
        if args.top_level_dir
        else None
    )
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    discovered, discover_errors = discover_test_ids(
        start_dir=start_dir,
        pattern=args.pattern,
        top_level_dir=top_level_dir,
    )
    include_modes = resolve_include_modes(args.include)
    effective_skip_tokens = resolve_effective_skip_tokens(args.skip_token, include_modes)
    effective_timeout_s = resolve_effective_timeout_s(
        timeout_s=args.timeout_s,
        include_modes=include_modes,
        include_strict_timeout_s=args.include_strict_timeout_s,
    )

    selected: list[str] = []
    skipped: list[tuple[str, str]] = []
    for test_id in discovered:
        reason = classify_skip(test_id, effective_skip_tokens, args.skip_regex)
        if reason:
            skipped.append((test_id, reason))
        else:
            selected.append(test_id)

    if args.max_tests is not None:
        selected = selected[: args.max_tests]

    discovered_out = audit_dir / "tests_discovered.txt"
    render_discovered_file(
        out_path=discovered_out,
        discovered=discovered,
        selected=selected,
        skipped=skipped,
        discover_errors=discover_errors,
    )

    runner = BoundedRunner(
        python_exe=args.python_exe,
        timeout_s=effective_timeout_s,
        cwd=repo_root,
        logs_dir=logs_dir,
    )

    for chunk in chunked(selected, args.chunk_size):
        runner.run_chunk_with_bisect(chunk, depth=0, kind="chunk")

    (
        observed_failures_total,
        observed_errors_total,
        _failing_test_ids,
        _error_test_ids,
        _top_failing_names,
    ) = collect_failure_error_stats(runner.results)

    summary_out = audit_dir / "tests_summary.md"
    render_summary_file(
        out_path=summary_out,
        discovered_count=len(discovered),
        selected_count=len(selected),
        skipped_count=len(skipped),
        discover_errors=discover_errors,
        results=runner.results,
        args=args,
        include_modes=include_modes,
        effective_skip_tokens=effective_skip_tokens,
        effective_timeout_s=effective_timeout_s,
    )

    flaky_out = audit_dir / "flaky_or_hanging_tests.md"
    render_flaky_hanging_file(out_path=flaky_out, results=runner.results)

    raw_out = audit_dir / "data" / "bounded_runner_results.json"
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    raw_out.write_text(
        json.dumps(
            {
                "config": {
                    "start_dir": args.start_dir,
                    "pattern": args.pattern,
                    "chunk_size": args.chunk_size,
                    "timeout_s": args.timeout_s,
                    "include_strict_timeout_s": args.include_strict_timeout_s,
                    "effective_timeout_s": effective_timeout_s,
                    "include_modes": include_modes,
                    "skip_tokens_configured": args.skip_token,
                    "skip_tokens_effective": effective_skip_tokens,
                    "skip_regex": args.skip_regex,
                    "max_tests": args.max_tests,
                    "python_exe": args.python_exe,
                },
                "discovered_total": len(discovered),
                "selected_total": len(selected),
                "skipped_total": len(skipped),
                "discover_errors": discover_errors,
                "observed_failures_total": observed_failures_total,
                "observed_errors_total": observed_errors_total,
                "results": [
                    {
                        "run_id": r.run_id,
                        "depth": r.depth,
                        "kind": r.kind,
                        "tests": r.tests,
                        "timeout": r.timeout,
                        "returncode": r.returncode,
                        "duration_s": r.duration_s,
                        "log_path": r.log_path,
                        "ran": r.ran,
                        "failures": r.failures,
                        "errors": r.errors,
                        "skipped": r.skipped,
                        "failed_test_ids": r.failed_test_ids,
                        "error_test_ids": r.error_test_ids,
                    }
                    for r in runner.results
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Console summary for quick operator feedback.
    print(f"Discovered: {len(discovered)}")
    print(f"Selected: {len(selected)}")
    print(f"Skipped by default filter: {len(skipped)}")
    print(f"Include modes: {', '.join(include_modes) if include_modes else 'none'}")
    print(f"Effective timeout_s: {effective_timeout_s}")
    print(f"Subprocess runs: {len(runner.results)}")
    print(f"Observed failures/errors: {observed_failures_total}/{observed_errors_total}")
    print(f"Artifacts:")
    print(f"  - {discovered_out.as_posix()}")
    print(f"  - {summary_out.as_posix()}")
    print(f"  - {flaky_out.as_posix()}")
    print(f"  - {raw_out.as_posix()}")
    if observed_failures_total > 0 or observed_errors_total > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
