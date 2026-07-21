from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _make_layout(tmp_path: Path, *, skew_ask: bool = False) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    source_bin = source / "bin"
    install_bin = tmp_path / "install-bin"
    project = tmp_path / "project"
    source_bin.mkdir(parents=True)
    install_bin.mkdir()
    (project / ".ccb").mkdir(parents=True)
    (project / ".ccb" / "ccb.config").write_text("claude:claude\n", encoding="utf-8")

    shutil.copy2(REPO_ROOT / "bin" / "ccbm", source_bin / "ccbm")
    (source_bin / "ccbm").chmod((source_bin / "ccbm").stat().st_mode | stat.S_IXUSR)

    _write_executable(
        source / "ccb",
        """#!/usr/bin/env bash
if [ "${1:-}" = "ps" ]; then
  echo "project_id: fake"
  echo "agent: name=claude state=idle provider=claude queue=0"
  exit 0
fi
echo "fake ccb $*"
""",
    )
    _write_executable(source_bin / "ask", "#!/usr/bin/env bash\necho fake ask\n")

    (install_bin / "ccb").symlink_to(source / "ccb")
    (install_bin / "ccbm").symlink_to(source_bin / "ccbm")
    if skew_ask:
        other = tmp_path / "other" / "bin"
        _write_executable(other / "ask", "#!/usr/bin/env bash\necho stale ask\n")
        (install_bin / "ask").symlink_to(other / "ask")
    else:
        (install_bin / "ask").symlink_to(source_bin / "ask")

    return source, install_bin, project


def _run_ccbm(install_bin: Path, project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{install_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run(
        [str(install_bin / "ccbm"), *args],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_ccbm_status_reports_consistent_toolchain(tmp_path: Path) -> None:
    source, install_bin, project = _make_layout(tmp_path)

    result = _run_ccbm(install_bin, project, "status")

    assert result.returncode == 0
    assert f"toolchain: ccb status=ok path={source / 'ccb'} expected={source / 'ccb'}" in result.stdout
    assert f"toolchain: ask status=ok path={source / 'bin' / 'ask'} expected={source / 'bin' / 'ask'}" in result.stdout
    assert f"toolchain: ccbm status=ok path={source / 'bin' / 'ccbm'} expected={source / 'bin' / 'ccbm'}" in result.stdout


def test_ccbm_status_does_not_depend_on_python3(tmp_path: Path) -> None:
    source, install_bin, project = _make_layout(tmp_path)
    poison_bin = tmp_path / "poison-bin"
    _write_executable(
        poison_bin / "python3",
        "#!/usr/bin/env bash\necho python3 must not be invoked >&2\nexit 99\n",
    )

    env_path = (
        f"{poison_bin}{os.pathsep}{install_bin}"
        f"{os.pathsep}{os.environ.get('PATH', '')}"
    )
    result = subprocess.run(
        [str(install_bin / "ccbm"), "status"],
        cwd=project,
        env={**os.environ, "PATH": env_path},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "python3 must not be invoked" not in result.stderr
    assert f"toolchain: ccb status=ok path={source / 'ccb'} expected={source / 'ccb'}" in result.stdout
    assert f"toolchain: ask status=ok path={source / 'bin' / 'ask'} expected={source / 'bin' / 'ask'}" in result.stdout
    assert f"toolchain: ccbm status=ok path={source / 'bin' / 'ccbm'} expected={source / 'bin' / 'ccbm'}" in result.stdout


def test_ccbm_status_exposes_stale_ask_split_brain(tmp_path: Path) -> None:
    source, install_bin, project = _make_layout(tmp_path, skew_ask=True)

    result = _run_ccbm(install_bin, project, "status")

    assert result.returncode == 0
    assert f"toolchain: ccb status=ok path={source / 'ccb'} expected={source / 'ccb'}" in result.stdout
    assert f"expected={source / 'bin' / 'ask'}" in result.stdout
    assert "toolchain: ask status=skew" in result.stdout


def test_ccbm_mount_refuses_stale_ask_split_brain_before_touching_daemon(tmp_path: Path) -> None:
    source, install_bin, project = _make_layout(tmp_path, skew_ask=True)

    result = _run_ccbm(install_bin, project)

    assert result.returncode == 2
    assert "ask resolves to a different CCB checkout" in result.stderr
    assert str(source / "bin" / "ask") in result.stderr
    assert "refusing to mount with split-brain CCB tooling" in result.stderr
    assert "fake ccb" not in result.stdout
