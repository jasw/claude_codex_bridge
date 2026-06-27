from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dynamic_layout_smoke.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("dynamic_layout_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_multi_node_config_declares_explicit_windows_and_loop_profiles() -> None:
    module = _load_module()

    text = module.build_multi_node_config()

    assert 'entry_window = "main"' in text
    assert '[windows]' in text
    assert 'main = "orchestrator:fake"' in text
    assert '[loop.capacity]' in text
    assert 'max_nodes = 4' in text
    assert '[loop.role_profiles.worker]' in text
    assert 'role = "agentroles.coder"' in text
    assert '[loop.role_profiles.code_reviewer]' in text
    assert 'role = "agentroles.code_reviewer"' in text


def test_build_window_class_config_declares_plan_orchestrate_window() -> None:
    module = _load_module()

    text = module.build_window_class_config()

    assert 'entry_window = "main"' in text
    assert '[windows]' in text
    assert 'main = "frontdesk:fake"' in text
    assert 'plan-orchestrate = "planner:fake"' in text


def test_prepare_projects_write_configs_and_roles(tmp_path: Path) -> None:
    module = _load_module()

    multi = module.prepare_multi_node_project(test_root=tmp_path, project_name="multi", reset=False)
    same = module.prepare_same_window_project(test_root=tmp_path, project_name="same", reset=False)
    window_class = module.prepare_window_class_project(test_root=tmp_path, project_name="window-class", reset=False)

    multi_root = Path(multi["project_root"])
    same_root = Path(same["project_root"])
    window_class_root = Path(window_class["project_root"])
    assert (multi_root / ".ccb" / "ccb.config").read_text(encoding="utf-8").startswith("version = 2")
    assert (same_root / ".ccb" / "ccb.config").read_text(encoding="utf-8").startswith("version = 2")
    assert 'plan-orchestrate = "planner:fake"' in (window_class_root / ".ccb" / "ccb.config").read_text(encoding="utf-8")
    assert (Path(multi["role_store"]) / "installed" / "agentroles.coder" / "current" / "role.toml").is_file()
    assert (Path(multi["role_store"]) / "installed" / "agentroles.code_reviewer" / "current" / "role.toml").is_file()
    assert (Path(same["role_store"]) / "installed" / "agentroles.general" / "current" / "role.toml").is_file()
    assert (Path(window_class["role_store"]) / "installed" / "agentroles.general" / "current" / "role.toml").is_file()


def test_payload_helpers_extract_window_agents_and_panes() -> None:
    module = _load_module()
    result = {
        "payload": {
            "windows": [
                {
                    "name": "main",
                    "agent_names": ["main", "helper1"],
                    "agents": [
                        {"agent": "main", "pane_id": "%1"},
                        {"agent": "helper1", "pane_id": "%2"},
                    ],
                }
            ]
        }
    }

    assert module._window_agents(result) == {"main": ["main", "helper1"]}
    assert module._agent_panes(result) == {"main": "%1", "helper1": "%2"}


def test_compact_payload_keeps_checks_and_window_summary_without_full_stdout() -> None:
    module = _load_module()
    payload = {
        "dynamic_layout_smoke_status": "ok",
        "checks": {"flow": True},
        "results": [
            {
                "flow": "flow",
                "flow_status": "ok",
                "checks": {"ok": True},
                "commands": [
                    {
                        "name": "layout",
                        "returncode": 0,
                        "stdout": "line1\nline2\nline3\nline4\n",
                        "payload": {
                            "layout_status": "ok",
                            "loop_agent_count": 2,
                            "windows": [
                                {
                                    "name": "node-round1-node1",
                                    "agent_names": ["worker", "checker"],
                                    "pane_count": 2,
                                    "large": "ignored",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }

    compact = module.compact_smoke_payload(payload)

    assert compact["dynamic_layout_smoke_status"] == "ok"
    command = compact["results"][0]["commands"][0]
    assert command["stdout_excerpt"] == ["line1", "line2", "line3"]
    assert command["payload"] == {
        "layout_status": "ok",
        "loop_agent_count": 2,
        "windows": [
            {
                "name": "node-round1-node1",
                "agents": ["worker", "checker"],
                "pane_count": 2,
            }
        ],
    }
