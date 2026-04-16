import os
import subprocess
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_BACKUP_SCRIPT = REPO_ROOT / "scripts" / "state-backup.sh"
STATE_RESTORE_SCRIPT = REPO_ROOT / "scripts" / "state-restore.sh"
DOCKER_LESS_PATH = "/usr/bin:/bin"


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _script_env(sandbox_root):
    return {
        **os.environ,
        "FLYMANAGER_ROOT_DIR": str(sandbox_root),
        "ENV_FILE": str(sandbox_root / "config" / "runtime.env"),
        "BACKUP_ROOT": str(sandbox_root / "backups"),
        "STATE_BACKUP_DIR": str(sandbox_root / "backups" / "state"),
        "RESTORE_PRECHECK_DIR": str(sandbox_root / "backups" / "restore-preflight"),
        "BACKUP_INCLUDE_CADDY": "0",
        "RESTORE_INCLUDE_CADDY": "0",
    }


def _run_script(script_path, *args, env, check=True):
    return subprocess.run(
        [str(script_path), *map(str, args)],
        cwd=REPO_ROOT,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def test_state_backup_and_restore_round_trip_preserves_manifest_and_preflight(tmp_path):
    sandbox_root = tmp_path / "sandbox"
    archive_path = sandbox_root / "backups" / "state" / "drill.tar.gz"
    env_path = sandbox_root / "config" / "runtime.env"
    data_file = sandbox_root / "data" / "specimens.txt"

    _write(env_path, "MODE=original\nSECRET=abc123\n")
    _write(data_file, "original data\n")
    _write(sandbox_root / "compose.yaml", "services:\n  app:\n    image: test\n")
    _write(sandbox_root / "compose.production.yaml", "services:\n  proxy:\n    image: caddy\n")
    _write(sandbox_root / "deploy" / "Caddyfile", "example.test {\n    respond \"ok\"\n}\n")

    env = _script_env(sandbox_root)
    _run_script(STATE_BACKUP_SCRIPT, archive_path, env=env)

    assert archive_path.exists()

    with tarfile.open(archive_path, "r:gz") as archive:
        manifest = archive.extractfile("./manifest.txt").read().decode("utf-8")
        archived_env = archive.extractfile("./runtime/env-file").read().decode("utf-8")
        archived_data = archive.extractfile("./runtime/data/specimens.txt").read().decode("utf-8")

    assert "env_restore_path=config/runtime.env" in manifest
    assert "includes_env=yes" in manifest
    assert "includes_data=yes" in manifest
    assert "includes_caddy=no" in manifest
    assert archived_env == "MODE=original\nSECRET=abc123\n"
    assert archived_data == "original data\n"

    env_path.write_text("MODE=mutated\nSECRET=changed\n", encoding="utf-8")
    data_file.write_text("mutated data\n", encoding="utf-8")
    _write(sandbox_root / "data" / "new-file.txt", "should disappear after restore\n")

    _run_script(STATE_RESTORE_SCRIPT, archive_path, env=env)

    assert env_path.read_text(encoding="utf-8") == "MODE=original\nSECRET=abc123\n"
    assert data_file.read_text(encoding="utf-8") == "original data\n"
    assert not (sandbox_root / "data" / "new-file.txt").exists()

    preflight_dirs = sorted((sandbox_root / "backups" / "restore-preflight").iterdir())
    assert len(preflight_dirs) == 1

    preflight_dir = preflight_dirs[0]
    assert (preflight_dir / "config" / "runtime.env").read_text(encoding="utf-8") == "MODE=mutated\nSECRET=changed\n"
    assert (preflight_dir / "data" / "specimens.txt").read_text(encoding="utf-8") == "mutated data\n"
    assert (preflight_dir / "data" / "new-file.txt").read_text(encoding="utf-8") == "should disappear after restore\n"
    assert (preflight_dir / "reference-config" / "compose.yaml").exists()
    assert (preflight_dir / "reference-config" / "compose.production.yaml").exists()
    assert (preflight_dir / "reference-config" / "deploy" / "Caddyfile").exists()


def test_state_backup_auto_skips_caddy_when_docker_is_unavailable(tmp_path):
    sandbox_root = tmp_path / "sandbox"
    archive_path = sandbox_root / "backups" / "state" / "auto-no-docker.tar.gz"

    _write(sandbox_root / "config" / "runtime.env", "MODE=original\n")
    _write(sandbox_root / "data" / "specimens.txt", "original data\n")
    _write(sandbox_root / "compose.yaml", "services:\n  app:\n    image: test\n")
    _write(sandbox_root / "compose.production.yaml", "services:\n  proxy:\n    image: caddy\n")
    _write(sandbox_root / "deploy" / "Caddyfile", "example.test {\n    respond \"ok\"\n}\n")

    env = {
        **_script_env(sandbox_root),
        "BACKUP_INCLUDE_CADDY": "auto",
        "PATH": DOCKER_LESS_PATH,
    }
    result = _run_script(STATE_BACKUP_SCRIPT, archive_path, env=env)

    assert "Backup written to" in result.stdout
    assert archive_path.exists()

    with tarfile.open(archive_path, "r:gz") as archive:
        manifest = archive.extractfile("./manifest.txt").read().decode("utf-8")

    assert "includes_caddy=no" in manifest


def test_state_restore_auto_warns_and_skips_caddy_without_docker(tmp_path):
    sandbox_root = tmp_path / "sandbox"
    archive_path = sandbox_root / "backups" / "state" / "restore-auto-no-docker.tar.gz"
    env_path = sandbox_root / "config" / "runtime.env"
    data_file = sandbox_root / "data" / "specimens.txt"

    _write(env_path, "MODE=mutated\n")
    _write(data_file, "mutated data\n")
    _write(sandbox_root / "data" / "extra.txt", "should disappear after restore\n")

    staging_root = tmp_path / "staging"
    _write(staging_root / "manifest.txt", "generated_at_utc=20260416T130248Z\nenv_restore_path=config/runtime.env\nincludes_env=yes\nincludes_data=yes\nincludes_caddy=yes\n")
    _write(staging_root / "runtime" / "env-file", "MODE=original\n")
    _write(staging_root / "runtime" / "data" / "specimens.txt", "original data\n")
    _write(staging_root / "runtime" / "caddy_data" / "certificates" / "dummy.txt", "placeholder\n")
    _write(staging_root / "runtime" / "caddy_config" / "autosave.json", "{}\n")
    _write(staging_root / "config" / "compose.yaml", "services:\n  app:\n    image: test\n")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(staging_root, arcname=".")

    env = {
        **_script_env(sandbox_root),
        "RESTORE_INCLUDE_CADDY": "auto",
        "PATH": DOCKER_LESS_PATH,
    }
    result = _run_script(STATE_RESTORE_SCRIPT, archive_path, env=env)

    assert "Skipping Caddy restore because Docker Compose is not available." in result.stderr
    assert env_path.read_text(encoding="utf-8") == "MODE=original\n"
    assert data_file.read_text(encoding="utf-8") == "original data\n"
    assert not (sandbox_root / "data" / "extra.txt").exists()

    preflight_dirs = sorted((sandbox_root / "backups" / "restore-preflight").iterdir())
    assert len(preflight_dirs) == 1
    assert (preflight_dirs[0] / "data" / "extra.txt").read_text(encoding="utf-8") == "should disappear after restore\n"