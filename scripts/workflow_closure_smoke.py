#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_ROOT = Path(os.environ.get("CCB_WORKFLOW_SMOKE_TEST_ROOT", "/home/bfly/yunwei/test_ccb2"))
DEFAULT_PLAN = "workflow-smoke"
DEFAULT_TASK = "task-closure"
ROLEPACK_ROOT = REPO_ROOT / "docs" / "plantree" / "plans" / "agentic-loop-workflow" / "drafts"
ROLEPACKS = {
    "agentroles.ccb_frontdesk": "agentroles.ccb_frontdesk",
    "agentroles.ccb_planner": "agentroles.ccb_planner",
    "agentroles.ccb_clarification_broker": "agentroles.ccb_clarification_broker",
    "agentroles.ccb_plan_reviewer": "agentroles.ccb_plan_reviewer",
    "agentroles.ccb_orchestrator": "agentroles.ccb_orchestrator",
    "agentroles.ccb_worker": "agentroles.ccb_worker",
    "agentroles.ccb_checker": "agentroles.ccb_checker",
    "agentroles.ccb_round_checker": "agentroles.ccb_round_checker",
}
TERMINAL_STATUSES = {"done", "partial", "replan_required", "blocked"}


def build_config(*, provider: str = "fake") -> str:
    return "\n".join(
        [
            (
                "frontdesk:{provider}; planner:{provider}; task_detailer:{provider}; clarification_broker:{provider}; "
                "plan_reviewer:{provider}; orchestrator:{provider}; round_checker:{provider}"
            ).format(provider=provider),
            "",
            "[agents.frontdesk]",
            'role = "agentroles.ccb_frontdesk"',
            "",
            "[agents.planner]",
            'role = "agentroles.ccb_planner"',
            "",
            "[agents.task_detailer]",
            'role = "agentroles.ccb_planner"',
            "",
            "[agents.clarification_broker]",
            'role = "agentroles.ccb_clarification_broker"',
            "",
            "[agents.plan_reviewer]",
            'role = "agentroles.ccb_plan_reviewer"',
            "",
            "[agents.orchestrator]",
            'role = "agentroles.ccb_orchestrator"',
            "",
            "[agents.round_checker]",
            'role = "agentroles.ccb_round_checker"',
            "",
            "[loop.capacity]",
            "enabled = true",
            "max_nodes = 4",
            'default_lifetime = "current_round"',
            'name_template = "loop-{loop_id}-{profile}-{index}"',
            'reuse = "prefer_idle"',
            "",
            "[loop.role_profiles.worker]",
            'role = "agentroles.ccb_worker"',
            f'provider = "{provider}"',
            'workspace_mode = "copy"',
            "max_instances = 2",
            'reuse = "prefer_idle"',
            "",
            "[loop.role_profiles.code_reviewer]",
            'role = "agentroles.ccb_checker"',
            f'provider = "{provider}"',
            'workspace_mode = "copy"',
            "max_instances = 2",
            'reuse = "prefer_idle"',
            "",
        ]
    )


def prepare_project(
    *,
    test_root: Path,
    project_name: str,
    provider: str,
    ccb_test: Path,
    reset: bool = False,
) -> dict[str, str]:
    root = test_root.expanduser().resolve(strict=False)
    project_root = (root / project_name).resolve(strict=False)
    if root not in project_root.parents and project_root != root:
        raise ValueError(f"project must be under test root: {root}")
    if reset and project_root.exists():
        shutil.rmtree(project_root)
    project_root.mkdir(parents=True, exist_ok=True)
    source_home = root / "source_home"
    source_home.mkdir(parents=True, exist_ok=True)
    config_path = project_root / ".ccb" / "ccb.config"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(build_config(provider=provider), encoding="utf-8")
    plan_root = project_root / "docs" / "plantree" / "plans" / DEFAULT_PLAN
    plan_root.mkdir(parents=True, exist_ok=True)
    (plan_root / "README.md").write_text("# Workflow Smoke Plan\n", encoding="utf-8")
    role_store = project_root / "roles"
    _install_rolepacks(role_store)
    shim_payload = _install_cli_shims(project_root=project_root, ccb_test=ccb_test)
    return {
        "project_root": str(project_root),
        "config_path": str(config_path),
        "plan_root": str(plan_root),
        "role_store": str(role_store),
        "source_home": str(source_home),
        **shim_payload,
    }


def run_workflow_smoke(
    *,
    test_root: Path,
    project_name: str,
    provider: str,
    ccb_test: Path,
    timeout_s: int,
    reset: bool = False,
    keep_running: bool = False,
) -> dict[str, Any]:
    prepared = prepare_project(
        test_root=test_root,
        project_name=project_name,
        provider=provider,
        ccb_test=ccb_test,
        reset=reset,
    )
    project_root = Path(prepared["project_root"])
    role_store = Path(prepared["role_store"])
    env = _smoke_env(test_root=test_root, project_root=project_root, role_store=role_store)
    results: list[dict[str, Any]] = []
    try:
        _append(results, "diagnose", [str(ccb_test), "--diagnose"], cwd=test_root, env=env)
        _append(results, "config_validate", [str(ccb_test), "--project", str(project_root), "config", "validate"], cwd=test_root, env=env)
        _append(results, "start", [str(ccb_test), "--project", str(project_root)], cwd=test_root, env=env)
        _append(
            results,
            "task_create",
            [
                str(ccb_test),
                "--project",
                str(project_root),
                "plan",
                "task-create",
                "--plan",
                DEFAULT_PLAN,
                "--title",
                "Workflow closure smoke task",
                "--task-id",
                DEFAULT_TASK,
                "--json",
            ],
            cwd=test_root,
            env=env,
        )
        _append_runner(results, "runner_planner_initial", ccb_test=ccb_test, project_root=project_root, test_root=test_root, env=env, timeout_s=timeout_s)

        artifacts = write_artifacts(project_root=project_root, task_id=DEFAULT_TASK)
        _append_question(results, "candidate_import", ccb_test, project_root, test_root, env, "candidate-import", artifacts["candidate_questions"])
        _append_question(results, "user_batch_import", ccb_test, project_root, test_root, env, "user-batch-import", artifacts["user_questions"])
        _append_runner(results, "runner_paused_for_frontdesk", ccb_test=ccb_test, project_root=project_root, test_root=test_root, env=env, timeout_s=timeout_s)
        _append_question(results, "raw_answer_import", ccb_test, project_root, test_root, env, "answer-import", artifacts["raw_answer"])
        _append_question(results, "normalized_import", ccb_test, project_root, test_root, env, "normalized-import", artifacts["normalized_answers"])
        _append_runner(results, "runner_planner_after_answers", ccb_test=ccb_test, project_root=project_root, test_root=test_root, env=env, timeout_s=timeout_s)

        for kind in ("brief", "requirements", "acceptance", "verification", "handoff"):
            _append_plan_artifact(results, kind, ccb_test, project_root, test_root, env, artifacts[kind])

        _append_runner(
            results,
            "runner_task_detailer",
            ccb_test=ccb_test,
            project_root=project_root,
            test_root=test_root,
            env=env,
            timeout_s=timeout_s,
            consume_role_output=True,
        )
        _append(
            results,
            "ready_before_review_rejected",
            [
                str(ccb_test),
                "--project",
                str(project_root),
                "plan",
                "task-status",
                "--task",
                DEFAULT_TASK,
                "--status",
                "ready",
                "--json",
            ],
            cwd=test_root,
            env=env,
            allow_failure=True,
        )
        _append_runner(results, "runner_plan_reviewer", ccb_test=ccb_test, project_root=project_root, test_root=test_root, env=env, timeout_s=timeout_s)
        _append_plan_artifact(results, "review", ccb_test, project_root, test_root, env, artifacts["review"])
        _append(
            results,
            "ready_after_review",
            [
                str(ccb_test),
                "--project",
                str(project_root),
                "plan",
                "task-status",
                "--task",
                DEFAULT_TASK,
                "--status",
                "ready",
                "--json",
            ],
            cwd=test_root,
            env=env,
        )
        _append_runner(results, "runner_execute", ccb_test=ccb_test, project_root=project_root, test_root=test_root, env=env, timeout_s=timeout_s)
        _append(
            results,
            "task_show_final",
            [str(ccb_test), "--project", str(project_root), "plan", "task-show", "--task", DEFAULT_TASK, "--json"],
            cwd=test_root,
            env=env,
        )
        _append(results, "post_execute_ps", [str(ccb_test), "--project", str(project_root), "ps"], cwd=test_root, env=env)
    finally:
        if not keep_running:
            _append(results, "kill", [str(ccb_test), "--project", str(project_root), "kill", "-f"], cwd=test_root, env=env, allow_failure=True)

    summary = _workflow_summary(results)
    return {
        "workflow_smoke_status": "ok" if summary["ok"] else "failed",
        "provider": provider,
        "project_root": str(project_root),
        "task_id": DEFAULT_TASK,
        "plan_slug": DEFAULT_PLAN,
        "summary": summary,
        "results": results,
    }


def write_artifacts(*, project_root: Path, task_id: str) -> dict[str, str]:
    drafts = project_root / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    files = {
        "candidate_questions": drafts / "candidate-questions.jsonl",
        "user_questions": drafts / "user-questions.json",
        "raw_answer": drafts / "raw-answer.md",
        "normalized_answers": drafts / "normalized-answers.jsonl",
        "brief": drafts / "brief.md",
        "requirements": drafts / "requirements.md",
        "acceptance": drafts / "acceptance.md",
        "verification": drafts / "verification.md",
        "handoff": drafts / "handoff.md",
        "review": drafts / "review.md",
    }
    files["candidate_questions"].write_text(
        json.dumps(
            {
                "id": "q1",
                "stage": "planning",
                "question": "Should this smoke keep fake-provider semantics?",
                "why_blocking": "The test must not require real provider auth.",
                "default_if_unanswered": "Use fake provider.",
                "defer_allowed": False,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    files["user_questions"].write_text(
        json.dumps(
            {
                "schema": "ccb.workflow.user_questions/v1",
                "task_id": task_id,
                "batch_id": "batch-closure",
                "questions": [
                    {
                        "id": "q1",
                        "text": "Confirm fake provider is acceptable for closure smoke.",
                        "why": "The smoke must be deterministic and account-free.",
                        "required": True,
                    }
                ],
                "defaults": [],
                "deferred": [],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    files["raw_answer"].write_text("Use fake provider for the workflow closure smoke.\n", encoding="utf-8")
    files["normalized_answers"].write_text(
        json.dumps(
            {
                "question_id": "q1",
                "answer": "Use fake provider.",
                "source": "user",
                "planner_note": "Proceed with deterministic fake-provider execution.",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    for kind in ("brief", "requirements", "acceptance", "verification", "handoff", "review"):
        files[kind].write_text(f"# {kind}\n\nWorkflow closure smoke artifact for {kind}.\n", encoding="utf-8")
    return {key: str(path) for key, path in files.items()}


def _workflow_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = {str(item["name"]): _json_payload(str(item.get("stdout") or "")) for item in results}
    runner_execute = payloads.get("runner_execute") or {}
    final_task = payloads.get("task_show_final") or {}
    round_path = _round_path(runner_execute)
    round_payload = _read_json_object(Path(round_path)) if round_path else {}
    release_payload = _nested_dict(round_payload, "capacity", "release")
    loop_id = str(runner_execute.get("loop_id") or round_payload.get("loop_id") or "")
    ps_text = str((next((item for item in results if item["name"] == "post_execute_ps"), {}) or {}).get("stdout") or "")
    checks = {
        "all_required_commands_succeeded": _required_commands_succeeded(results),
        "ready_before_review_rejected": _returncode(results, "ready_before_review_rejected") not in (0, None),
        "planner_initial_activated": (payloads.get("runner_planner_initial") or {}).get("action") == "activated_planner",
        "needs_clarification_reached": (payloads.get("user_batch_import") or {}).get("task_status") == "needs_clarification",
        "frontdesk_pause_reached": (payloads.get("runner_paused_for_frontdesk") or {}).get("loop_runner_status") == "paused",
        "normalized_answers_returned_to_draft": (payloads.get("normalized_import") or {}).get("task_status") == "draft",
        "planner_after_answers_activated": (payloads.get("runner_planner_after_answers") or {}).get("action") == "activated_planner",
        "task_detailer_imported": (payloads.get("runner_task_detailer") or {}).get("action") == "imported_task_detailer_output",
        "task_detailer_detail_ready": (payloads.get("runner_task_detailer") or {}).get("task_status") == "detail_ready",
        "plan_reviewer_activated": (payloads.get("runner_plan_reviewer") or {}).get("action") == "activated_plan_reviewer",
        "ready_after_review": (payloads.get("ready_after_review") or {}).get("status") == "ready",
        "execution_round_ran": runner_execute.get("action") == "ran_one_round",
        "terminal_or_replan_status": str((final_task.get("task") or {}).get("status") or final_task.get("status") or "") in TERMINAL_STATUSES,
        "release_policy_auto": release_payload.get("release_policy") == "auto",
        "release_retained_zero": int(release_payload.get("retained_count") or 0) == 0,
        "release_status_released": release_payload.get("loop_capacity_status") == "released",
        "dynamic_agents_absent_from_ps": bool(loop_id) and f"loop-{loop_id}-worker" not in ps_text and f"loop-{loop_id}-code_reviewer" not in ps_text,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "loop_id": loop_id,
        "round_path": round_path,
        "round_result": runner_execute.get("round_result"),
        "round_result_source": runner_execute.get("round_result_source"),
        "final_status": str((final_task.get("task") or {}).get("status") or final_task.get("status") or ""),
        "release": release_payload,
    }


def _append_question(
    results: list[dict[str, Any]],
    name: str,
    ccb_test: Path,
    project_root: Path,
    test_root: Path,
    env: dict[str, str],
    action: str,
    file_path: str,
) -> None:
    _append(
        results,
        name,
        [str(ccb_test), "--project", str(project_root), "question", action, "--task", DEFAULT_TASK, "--file", file_path, "--json"],
        cwd=test_root,
        env=env,
    )


def _append_plan_artifact(
    results: list[dict[str, Any]],
    kind: str,
    ccb_test: Path,
    project_root: Path,
    test_root: Path,
    env: dict[str, str],
    file_path: str,
) -> None:
    _append(
        results,
        f"artifact_{kind}",
        [
            str(ccb_test),
            "--project",
            str(project_root),
            "plan",
            "task-artifact",
            "--task",
            DEFAULT_TASK,
            "--kind",
            kind,
            "--file",
            file_path,
            "--json",
        ],
        cwd=test_root,
        env=env,
    )


def _append_runner(
    results: list[dict[str, Any]],
    name: str,
    *,
    ccb_test: Path,
    project_root: Path,
    test_root: Path,
    env: dict[str, str],
    timeout_s: int,
    consume_role_output: bool = False,
) -> None:
    command = [str(ccb_test), "--project", str(project_root), "loop", "runner", "--once", "--timeout", str(timeout_s), "--json"]
    if consume_role_output:
        command.insert(-1, "--consume-role-output")
    _append(
        results,
        name,
        command,
        cwd=test_root,
        env=env,
        timeout=timeout_s + 60,
    )


def _append(
    results: list[dict[str, Any]],
    name: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 60,
    allow_failure: bool = False,
) -> None:
    completed = _run_command(name, command, cwd=cwd, env=env, timeout=timeout)
    results.append(completed)
    if completed["returncode"] not in (0, None) and not allow_failure:
        raise RuntimeError(f"{name} failed: {completed['stderr'] or completed['stdout']}")


def _run_command(name: str, command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, cwd=str(cwd), env=env, text=True, capture_output=True, timeout=timeout)
        return {
            "name": name,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "command": command,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "timeout": True,
        }


def _required_commands_succeeded(results: list[dict[str, Any]]) -> bool:
    for item in results:
        name = str(item.get("name") or "")
        if name in {"ready_before_review_rejected", "kill"}:
            continue
        if item.get("returncode") != 0:
            return False
    return True


def _returncode(results: list[dict[str, Any]], name: str) -> int | None:
    for item in results:
        if item.get("name") == name:
            value = item.get("returncode")
            return int(value) if value is not None else None
    return None


def _round_path(payload: dict[str, Any]) -> str:
    round_info = payload.get("round")
    if isinstance(round_info, dict) and str(round_info.get("round_path") or "").strip():
        return str(round_info["round_path"])
    paths = payload.get("paths")
    if isinstance(paths, dict) and str(paths.get("round") or "").strip():
        return str(paths["round"])
    return ""


def _nested_dict(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_payload(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _install_rolepacks(role_store: Path) -> None:
    for role_id, dirname in ROLEPACKS.items():
        source = ROLEPACK_ROOT / dirname
        if not source.is_dir():
            raise FileNotFoundError(f"rolepack source missing: {source}")
        target = role_store / "installed" / role_id / "current"
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)


def _install_cli_shims(*, project_root: Path, ccb_test: Path) -> dict[str, str]:
    bin_dir = project_root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ccb_test_path = ccb_test.expanduser().resolve(strict=False)
    payload: dict[str, str] = {"bin_dir": str(bin_dir)}
    for name, args in {"ccb": "", "ask": " ask"}.items():
        shim = bin_dir / name
        shim.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    f'exec "{ccb_test_path}"{args} "$@"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        shim.chmod(0o755)
        payload[f"{name}_shim"] = str(shim)
    return payload


def _smoke_env(*, test_root: Path, project_root: Path, role_store: Path) -> dict[str, str]:
    env = dict(os.environ)
    source_home = test_root.expanduser().resolve(strict=False) / "source_home"
    env["HOME"] = str(source_home)
    env["CCB_SOURCE_HOME"] = str(source_home)
    env["AGENT_ROLES_STORE"] = str(role_store)
    env["CCB_NO_ATTACH"] = "1"
    env["CCB_REPLY_LANG"] = "en"
    env["PATH"] = str(project_root / "bin") + os.pathsep + env.get("PATH", "")
    return env


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a deterministic CCB workflow closure smoke.")
    parser.add_argument("--test-root", default=str(DEFAULT_TEST_ROOT))
    parser.add_argument("--project-name", default="workflow-closure-smoke")
    parser.add_argument("--provider", default="fake")
    parser.add_argument("--ccb-test", default=str(REPO_ROOT / "ccb_test"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--keep-running", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    test_root = Path(args.test_root)
    ccb_test = Path(args.ccb_test)
    prepared = prepare_project(
        test_root=test_root,
        project_name=args.project_name,
        provider=args.provider,
        ccb_test=ccb_test,
        reset=bool(args.reset),
    )
    payload: dict[str, Any] = {"prepare": prepared}
    if args.run:
        payload["run"] = run_workflow_smoke(
            test_root=test_root,
            project_name=args.project_name,
            provider=args.provider,
            ccb_test=ccb_test,
            timeout_s=int(args.timeout),
            reset=False,
            keep_running=bool(args.keep_running),
        )
    if args.json or args.prepare_only or args.run:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"project_root: {prepared['project_root']}")
        print(f"role_store: {prepared['role_store']}")
        print("run: add --run --json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
