#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_ROOT = Path(os.environ.get("CCB_DYNAMIC_LAYOUT_SMOKE_TEST_ROOT", "/home/bfly/yunwei/test_ccb2"))
DEFAULT_CCB_TEST = REPO_ROOT / "ccb_test"


def build_multi_node_config() -> str:
    return "\n".join(
        [
            "version = 2",
            'entry_window = "main"',
            "",
            "[windows]",
            'main = "orchestrator:fake"',
            "",
            "[loop.capacity]",
            "enabled = true",
            "max_nodes = 4",
            'default_lifetime = "current_round"',
            'name_template = "loop-{loop_id}-{profile}-{index}"',
            'reuse = "prefer_idle"',
            "",
            "[loop.role_profiles.worker]",
            'role = "agentroles.coder"',
            'provider = "fake"',
            'thinking = "medium"',
            'workspace_mode = "inplace"',
            "max_instances = 2",
            'reuse = "prefer_idle"',
            "",
            "[loop.role_profiles.code_reviewer]",
            'role = "agentroles.code_reviewer"',
            'provider = "fake"',
            'thinking = "medium"',
            'workspace_mode = "inplace"',
            "max_instances = 2",
            "",
        ]
    )


def build_same_window_config() -> str:
    return "\n".join(
        [
            "version = 2",
            'entry_window = "main"',
            "",
            "[windows]",
            'main = "main:fake"',
            "",
        ]
    )


def build_window_class_config() -> str:
    return "\n".join(
        [
            "version = 2",
            'entry_window = "main"',
            "",
            "[windows]",
            'main = "frontdesk:fake"',
            'plan-orchestrate = "planner:fake"',
            "",
        ]
    )


def prepare_multi_node_project(*, test_root: Path, project_name: str, reset: bool = False) -> dict[str, str]:
    project_root = _project_root(test_root, project_name)
    if reset and project_root.exists():
        shutil.rmtree(project_root)
    (project_root / ".ccb").mkdir(parents=True, exist_ok=True)
    (project_root / ".ccb" / "ccb.config").write_text(build_multi_node_config(), encoding="utf-8")
    role_store = project_root / "roles"
    _write_minimal_role(role_store, "agentroles.coder", default_agent_name="worker")
    _write_minimal_role(role_store, "agentroles.code_reviewer", default_agent_name="code_reviewer")
    return {"project_root": str(project_root), "role_store": str(role_store)}


def prepare_same_window_project(*, test_root: Path, project_name: str, reset: bool = False) -> dict[str, str]:
    project_root = _project_root(test_root, project_name)
    if reset and project_root.exists():
        shutil.rmtree(project_root)
    (project_root / ".ccb").mkdir(parents=True, exist_ok=True)
    (project_root / ".ccb" / "ccb.config").write_text(build_same_window_config(), encoding="utf-8")
    role_store = project_root / "roles"
    _write_minimal_role(role_store, "agentroles.general", default_agent_name="general")
    return {"project_root": str(project_root), "role_store": str(role_store)}


def prepare_window_class_project(*, test_root: Path, project_name: str, reset: bool = False) -> dict[str, str]:
    project_root = _project_root(test_root, project_name)
    if reset and project_root.exists():
        shutil.rmtree(project_root)
    (project_root / ".ccb").mkdir(parents=True, exist_ok=True)
    (project_root / ".ccb" / "ccb.config").write_text(build_window_class_config(), encoding="utf-8")
    role_store = project_root / "roles"
    _write_minimal_role(role_store, "agentroles.general", default_agent_name="general")
    return {"project_root": str(project_root), "role_store": str(role_store)}


def run_dynamic_layout_smoke(
    *,
    test_root: Path,
    project_prefix: str,
    ccb_test: Path,
    reset: bool = False,
    keep_running: bool = False,
) -> dict[str, Any]:
    test_root = test_root.expanduser().resolve(strict=False)
    test_root.mkdir(parents=True, exist_ok=True)
    source_home = test_root / "source_home"
    source_home.mkdir(parents=True, exist_ok=True)
    results = [
        _run_multi_node_flow(
            test_root=test_root,
            project_name=f"{project_prefix}-multi-node",
            ccb_test=ccb_test,
            source_home=source_home,
            reset=reset,
            keep_running=keep_running,
        ),
        _run_same_window_flow(
            test_root=test_root,
            project_name=f"{project_prefix}-same-window",
            ccb_test=ccb_test,
            source_home=source_home,
            reset=reset,
            keep_running=keep_running,
        ),
        _run_window_class_flow(
            test_root=test_root,
            project_name=f"{project_prefix}-window-class",
            ccb_test=ccb_test,
            source_home=source_home,
            reset=reset,
            keep_running=keep_running,
        ),
    ]
    checks = {item["flow"]: item.get("flow_status") == "ok" for item in results}
    return {
        "dynamic_layout_smoke_status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "results": results,
    }


def _run_multi_node_flow(
    *,
    test_root: Path,
    project_name: str,
    ccb_test: Path,
    source_home: Path,
    reset: bool,
    keep_running: bool,
) -> dict[str, Any]:
    prepared = prepare_multi_node_project(test_root=test_root, project_name=project_name, reset=reset)
    project_root = Path(prepared["project_root"])
    env = _env(source_home=source_home, role_store=Path(prepared["role_store"]))
    commands: list[dict[str, Any]] = []
    try:
        commands.append(_run("config_validate", [str(ccb_test), "--project", str(project_root), "config", "validate"], cwd=test_root, env=env))
        commands.append(_run("start", [str(ccb_test), "--project", str(project_root)], cwd=test_root, env=env))
        ensure = _run_json(
            "ensure_multi_node",
            [
                str(ccb_test),
                "--project",
                str(project_root),
                "loop",
                "capacity",
                "ensure",
                "--loop-id",
                "round2",
                "--profile",
                "worker=2",
                "--profile",
                "code_reviewer=2",
                "--json",
            ],
            cwd=test_root,
            env=env,
        )
        commands.append(ensure)
        before = _run_json("layout_before_release", [str(ccb_test), "--project", str(project_root), "layout", "status", "--json"], cwd=test_root, env=env)
        commands.append(before)
        worker_ask = _run("ask_worker1", [str(ccb_test), "--project", str(project_root), "ask", "loop-round2-worker-1"], cwd=test_root, env=env, input_text="dynamic layout smoke ping worker1\n")
        reviewer_ask = _run("ask_reviewer2", [str(ccb_test), "--project", str(project_root), "ask", "loop-round2-code_reviewer-2"], cwd=test_root, env=env, input_text="dynamic layout smoke ping reviewer2\n")
        commands.extend([worker_ask, reviewer_ask])
        commands.extend(
            _watch_submitted_jobs(
                ccb_test=ccb_test,
                project_root=project_root,
                test_root=test_root,
                env=env,
                asks=(worker_ask, reviewer_ask),
            )
        )
        release = _run_json(
            "release_multi_node",
            [
                str(ccb_test),
                "--project",
                str(project_root),
                "loop",
                "capacity",
                "release",
                "--loop-id",
                "round2",
                "--idle-only",
                "--json",
            ],
            cwd=test_root,
            env=env,
        )
        commands.append(release)
        after = _run_json("layout_after_release", [str(ccb_test), "--project", str(project_root), "layout", "status", "--json"], cwd=test_root, env=env)
        commands.append(after)
        checks = {
            "ensure_add_window": _payload(ensure).get("apply", {}).get("plan_class") == "add_window",
            "four_loop_agents": _payload(before).get("loop_agent_count") == 4,
            "two_node_windows": _has_windows(
                before,
                {
                "node-round2-node1": ["loop-round2-worker-1", "loop-round2-code_reviewer-1"],
                "node-round2-node2": ["loop-round2-worker-2", "loop-round2-code_reviewer-2"],
                },
            ),
            "asks_accepted": _accepted(worker_ask) and _accepted(reviewer_ask),
            "asks_terminal": _watch_commands_terminal(commands),
            "release_removed_four": _payload(release).get("released_count") == 4,
            "loop_agents_cleaned": _payload(after).get("loop_agent_count") == 0,
            "returned_to_main": _window_agents(after) == {"main": ["orchestrator"]},
        }
        status = "ok" if all(checks.values()) and _all_success(commands) else "failed"
        return {"flow": "multi_node_capacity", "flow_status": status, "checks": checks, "commands": commands}
    finally:
        if not keep_running:
            commands.append(_run("kill", [str(ccb_test), "--project", str(project_root), "kill", "-f"], cwd=test_root, env=env))


def _run_same_window_flow(
    *,
    test_root: Path,
    project_name: str,
    ccb_test: Path,
    source_home: Path,
    reset: bool,
    keep_running: bool,
) -> dict[str, Any]:
    prepared = prepare_same_window_project(test_root=test_root, project_name=project_name, reset=reset)
    project_root = Path(prepared["project_root"])
    env = _env(source_home=source_home, role_store=Path(prepared["role_store"]))
    commands: list[dict[str, Any]] = []
    try:
        commands.append(_run("config_validate", [str(ccb_test), "--project", str(project_root), "config", "validate"], cwd=test_root, env=env))
        commands.append(_run("start", [str(ccb_test), "--project", str(project_root)], cwd=test_root, env=env))
        for helper in ("helper1", "helper2", "helper3"):
            commands.append(
                _run_json(
                    f"add_{helper}",
                    [
                        str(ccb_test),
                        "--project",
                        str(project_root),
                        "agent",
                        "add",
                        f"{helper}:fake",
                        "--role",
                        "agentroles.general",
                        "--window",
                        "main",
                        "--hidden",
                        "--json",
                    ],
                    cwd=test_root,
                    env=env,
                )
            )
        before = _run_json("layout_before_middle_release", [str(ccb_test), "--project", str(project_root), "layout", "status", "--json"], cwd=test_root, env=env)
        commands.append(before)
        release = _run_json(
            "remove_middle_helper",
            [
                str(ccb_test),
                "--project",
                str(project_root),
                "agent",
                "remove",
                "helper2",
                "--policy",
                "unload",
                "--idle-only",
                "--json",
            ],
            cwd=test_root,
            env=env,
        )
        commands.append(release)
        after = _run_json("layout_after_middle_release", [str(ccb_test), "--project", str(project_root), "layout", "status", "--json"], cwd=test_root, env=env)
        commands.append(after)
        commands.append(_run("ask_helper1", [str(ccb_test), "--project", str(project_root), "ask", "helper1"], cwd=test_root, env=env, input_text="same-window smoke ping helper1\n"))
        commands.append(_run("ask_helper3", [str(ccb_test), "--project", str(project_root), "ask", "helper3"], cwd=test_root, env=env, input_text="same-window smoke ping helper3\n"))
        before_panes = _agent_panes(before)
        after_panes = _agent_panes(after)
        checks = {
            "add_agent_panes": [_payload(item).get("apply", {}).get("plan_class") for item in commands[2:5]] == ["add_agent", "add_agent", "add_agent"],
            "before_order": _window_agents(before).get("main") == ["main", "helper1", "helper2", "helper3"],
            "remove_agent_plan": _payload(release).get("apply", {}).get("plan_class") == "remove_agent",
            "removed_middle_pane": _payload(release).get("applied", {}).get("removed_pane_id") == before_panes.get("helper2"),
            "reflowed_main_window": _payload(release).get("apply", {}).get("namespace_reflowed_windows") == ["main"],
            "survivor_panes_preserved": after_panes.get("helper1") == before_panes.get("helper1") and after_panes.get("helper3") == before_panes.get("helper3"),
            "after_order": _window_agents(after).get("main") == ["main", "helper1", "helper3"],
            "asks_accepted": _accepted(commands[-2]) and _accepted(commands[-1]),
        }
        status = "ok" if all(checks.values()) and _all_success(commands) else "failed"
        return {"flow": "same_window_middle_release", "flow_status": status, "checks": checks, "commands": commands}
    finally:
        if not keep_running:
            commands.append(_run("kill", [str(ccb_test), "--project", str(project_root), "kill", "-f"], cwd=test_root, env=env))


def _run_window_class_flow(
    *,
    test_root: Path,
    project_name: str,
    ccb_test: Path,
    source_home: Path,
    reset: bool,
    keep_running: bool,
) -> dict[str, Any]:
    prepared = prepare_window_class_project(test_root=test_root, project_name=project_name, reset=reset)
    project_root = Path(prepared["project_root"])
    env = _env(source_home=source_home, role_store=Path(prepared["role_store"]))
    commands: list[dict[str, Any]] = []
    try:
        commands.append(_run("config_validate", [str(ccb_test), "--project", str(project_root), "config", "validate"], cwd=test_root, env=env))
        commands.append(_run("start", [str(ccb_test), "--project", str(project_root)], cwd=test_root, env=env))
        for helper in ("planner_helper1", "planner_helper2", "planner_helper3"):
            commands.append(
                _run_json(
                    f"add_{helper}",
                    [
                        str(ccb_test),
                        "--project",
                        str(project_root),
                        "agent",
                        "add",
                        f"{helper}:fake",
                        "--role",
                        "agentroles.general",
                        "--window-class",
                        "plan-orchestrate",
                        "--hidden",
                        "--json",
                    ],
                    cwd=test_root,
                    env=env,
                )
            )
        before = _run_json("layout_before_window_class_release", [str(ccb_test), "--project", str(project_root), "layout", "status", "--json"], cwd=test_root, env=env)
        commands.append(before)
        release = _run_json(
            "remove_middle_window_class_helper",
            [
                str(ccb_test),
                "--project",
                str(project_root),
                "agent",
                "remove",
                "planner_helper2",
                "--policy",
                "unload",
                "--idle-only",
                "--json",
            ],
            cwd=test_root,
            env=env,
        )
        commands.append(release)
        after = _run_json("layout_after_window_class_release", [str(ccb_test), "--project", str(project_root), "layout", "status", "--json"], cwd=test_root, env=env)
        commands.append(after)
        commands.append(
            _run(
                "ask_planner_helper1",
                [str(ccb_test), "--project", str(project_root), "ask", "planner_helper1"],
                cwd=test_root,
                env=env,
                input_text="window-class smoke ping planner_helper1\n",
            )
        )
        commands.append(
            _run(
                "ask_planner_helper3",
                [str(ccb_test), "--project", str(project_root), "ask", "planner_helper3"],
                cwd=test_root,
                env=env,
                input_text="window-class smoke ping planner_helper3\n",
            )
        )
        before_panes = _agent_panes(before)
        after_panes = _agent_panes(after)
        checks = {
            "add_agent_panes": [_payload(item).get("apply", {}).get("plan_class") for item in commands[2:5]] == ["add_agent", "add_agent", "add_agent"],
            "before_main_order": _window_agents(before).get("main") == ["frontdesk"],
            "before_plan_order": _window_agents(before).get("plan-orchestrate")
            == ["planner", "planner_helper1", "planner_helper2", "planner_helper3"],
            "remove_agent_plan": _payload(release).get("apply", {}).get("plan_class") == "remove_agent",
            "removed_middle_pane": _payload(release).get("applied", {}).get("removed_pane_id") == before_panes.get("planner_helper2"),
            "reflowed_plan_window": _payload(release).get("apply", {}).get("namespace_reflowed_windows") == ["plan-orchestrate"],
            "survivor_panes_preserved": after_panes.get("planner_helper1") == before_panes.get("planner_helper1")
            and after_panes.get("planner_helper3") == before_panes.get("planner_helper3"),
            "after_main_order": _window_agents(after).get("main") == ["frontdesk"],
            "after_plan_order": _window_agents(after).get("plan-orchestrate") == ["planner", "planner_helper1", "planner_helper3"],
            "asks_accepted": _accepted(commands[-2]) and _accepted(commands[-1]),
        }
        status = "ok" if all(checks.values()) and _all_success(commands) else "failed"
        return {"flow": "window_class_middle_release", "flow_status": status, "checks": checks, "commands": commands}
    finally:
        if not keep_running:
            commands.append(_run("kill", [str(ccb_test), "--project", str(project_root), "kill", "-f"], cwd=test_root, env=env))


def _project_root(test_root: Path, project_name: str) -> Path:
    root = test_root.expanduser().resolve(strict=False)
    project_root = (root / project_name).resolve(strict=False)
    if root not in project_root.parents and project_root != root:
        raise ValueError(f"project must be under test root: {root}")
    return project_root


def _write_minimal_role(role_store: Path, role_id: str, *, default_agent_name: str) -> None:
    target = role_store / "installed" / role_id / "current" / "role.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            [
                f'id = "{role_id}"',
                'version = "0.1.0"',
                "",
                "[identity]",
                f'default_agent_name = "{default_agent_name}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _env(*, source_home: Path, role_store: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(source_home)
    env["CCB_SOURCE_HOME"] = str(source_home)
    env["AGENT_ROLES_STORE"] = str(role_store)
    env["CCB_NO_ATTACH"] = "1"
    env["CCB_WATCH_TIMEOUT_S"] = "10"
    env["CCB_WATCH_POLL_INTERVAL_S"] = "0.1"
    return env


def _run_json(name: str, command: list[str], *, cwd: Path, env: dict[str, str], input_text: str | None = None) -> dict[str, Any]:
    result = _run(name, command, cwd=cwd, env=env, input_text=input_text)
    try:
        payload = json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError:
        payload = None
    result["payload"] = payload if isinstance(payload, dict) else {}
    return result


def _run(name: str, command: list[str], *, cwd: Path, env: dict[str, str], input_text: str | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        input=input_text,
        capture_output=True,
        timeout=60,
    )
    return {
        "name": name,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload")
    return payload if isinstance(payload, dict) else {}


def compact_smoke_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a small CLI-friendly copy of a full smoke result payload."""
    return {
        "dynamic_layout_smoke_status": payload.get("dynamic_layout_smoke_status"),
        "checks": payload.get("checks"),
        "results": [_compact_flow_result(item) for item in payload.get("results", []) if isinstance(item, dict)],
    }


def _compact_flow_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "flow": result.get("flow"),
        "flow_status": result.get("flow_status"),
        "checks": result.get("checks"),
        "commands": [_compact_command(item) for item in result.get("commands", []) if isinstance(item, dict)],
    }


def _compact_command(command: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "name": command.get("name"),
        "returncode": command.get("returncode"),
    }
    payload_summary = _compact_payload_summary(_payload(command))
    if payload_summary:
        summary["payload"] = payload_summary
    stdout_excerpt = _line_excerpt(str(command.get("stdout") or ""))
    stderr_excerpt = _line_excerpt(str(command.get("stderr") or ""))
    if stdout_excerpt:
        summary["stdout_excerpt"] = stdout_excerpt
    if stderr_excerpt:
        summary["stderr_excerpt"] = stderr_excerpt
    return summary


def _compact_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    keys = (
        "action",
        "layout_status",
        "loop_capacity_status",
        "agent_lifecycle_status",
        "agent_count",
        "runtime_agent_count",
        "loop_agent_count",
        "dynamic_agent_count",
        "window_count",
        "pane_count",
        "released_count",
        "retained_count",
        "retained_busy",
    )
    summary = {key: payload.get(key) for key in keys if key in payload}
    apply_payload = payload.get("apply")
    if isinstance(apply_payload, dict):
        summary["apply"] = {
            key: apply_payload.get(key)
            for key in ("plan_class", "apply_status", "reload_status")
            if key in apply_payload
        }
        if apply_payload.get("namespace_reflowed_windows"):
            summary["apply"]["namespace_reflowed_windows"] = list(apply_payload.get("namespace_reflowed_windows") or ())
        if apply_payload.get("namespace_reflow_errors"):
            summary["apply"]["namespace_reflow_errors"] = dict(apply_payload.get("namespace_reflow_errors") or {})
    windows = payload.get("windows")
    if isinstance(windows, list):
        summary["windows"] = [
            {
                "name": raw.get("name"),
                "agents": raw.get("agent_names"),
                "pane_count": raw.get("pane_count"),
            }
            for raw in windows
            if isinstance(raw, dict)
        ]
    return summary


def _line_excerpt(text: str, *, max_lines: int = 3, max_chars: int = 240) -> list[str]:
    if not text:
        return []
    lines = [line for line in text.strip().splitlines() if line][:max_lines]
    return [line[:max_chars] for line in lines]


def _window_agents(result: dict[str, Any]) -> dict[str, list[str]]:
    payload = _payload(result)
    windows = payload.get("windows")
    if not isinstance(windows, list):
        return {}
    values: dict[str, list[str]] = {}
    for raw_window in windows:
        if not isinstance(raw_window, dict):
            continue
        name = str(raw_window.get("name") or "")
        agents = raw_window.get("agent_names")
        if name and isinstance(agents, list):
            values[name] = [str(item) for item in agents]
    return values


def _has_windows(result: dict[str, Any], expected: dict[str, list[str]]) -> bool:
    actual = _window_agents(result)
    return all(actual.get(name) == agents for name, agents in expected.items())


def _agent_panes(result: dict[str, Any]) -> dict[str, str]:
    payload = _payload(result)
    panes: dict[str, str] = {}
    for raw_window in payload.get("windows") if isinstance(payload.get("windows"), list) else []:
        if not isinstance(raw_window, dict):
            continue
        agents = raw_window.get("agents")
        if not isinstance(agents, list):
            continue
        for raw_agent in agents:
            if not isinstance(raw_agent, dict):
                continue
            agent = str(raw_agent.get("agent") or "")
            pane_id = str(raw_agent.get("pane_id") or "")
            if agent and pane_id:
                panes[agent] = pane_id
    return panes


def _accepted(result: dict[str, Any]) -> bool:
    return int(result.get("returncode") or 0) == 0 and "accepted job=" in str(result.get("stdout") or "")


def _watch_submitted_jobs(
    *,
    ccb_test: Path,
    project_root: Path,
    test_root: Path,
    env: dict[str, str],
    asks: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for ask in asks:
        job_id = _job_id(ask)
        if job_id is None:
            continue
        results.append(
            _run(
                f"watch_{job_id}",
                [str(ccb_test), "--project", str(project_root), "watch", job_id],
                cwd=test_root,
                env=env,
            )
        )
    return results


def _job_id(result: dict[str, Any]) -> str | None:
    match = re.search(r"\bjob=(job_[A-Za-z0-9_-]+)\b", str(result.get("stdout") or ""))
    return match.group(1) if match else None


def _watch_commands_terminal(results: list[dict[str, Any]]) -> bool:
    watch_results = [item for item in results if str(item.get("name") or "").startswith("watch_job_")]
    return bool(watch_results) and all(
        int(item.get("returncode") or 0) == 0 and "watch_status: terminal" in str(item.get("stdout") or "")
        for item in watch_results
    )


def _all_success(results: list[dict[str, Any]]) -> bool:
    return all(int(item.get("returncode") or 0) == 0 for item in results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run CCB dynamic window/pane layout smoke tests with fake providers.")
    parser.add_argument("--test-root", type=Path, default=DEFAULT_TEST_ROOT)
    parser.add_argument("--project-prefix", default="dynamic-layout-smoke")
    parser.add_argument("--ccb-test", type=Path, default=DEFAULT_CCB_TEST)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--keep-running", action="store_true")
    parser.add_argument("--full-output", action="store_true", help="Print complete command stdout and JSON payloads.")
    args = parser.parse_args(argv)

    payload = run_dynamic_layout_smoke(
        test_root=args.test_root,
        project_prefix=args.project_prefix,
        ccb_test=args.ccb_test,
        reset=args.reset,
        keep_running=args.keep_running,
    )
    printable = payload if args.full_output else compact_smoke_payload(payload)
    print(json.dumps(printable, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("dynamic_layout_smoke_status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
