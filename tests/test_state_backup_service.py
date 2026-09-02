"""The state backup, run from the app's own scheduler.

mongodump covers every collection; it does not cover the files on disk, nor
the process environment -- which is the only place SECRET_KEY, the admin
credentials and the SMTP credentials exist. This used to need a DSM Task
Scheduler entry, i.e. a dependency living outside the stack that a migration
can lose. It now runs on the app's APScheduler under the same distributed
lock as the flip reminders.

The app only PRODUCES archives. Pruning and uploading stay in the backup
container, which already owns the GFS retention rule and the rclone config --
one implementation of the deletion logic, not two.
"""
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from flymanager.app.services.state_backup import (build_state_backup,
                                                  STATE_BACKUP_SOURCES)


@pytest.fixture
def keypair(tmp_path):
    priv, pub = tmp_path / "priv.pem", tmp_path / "pub.pem"
    subprocess.run(["openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
                    "-keyout", str(priv), "-out", str(pub), "-days", "1",
                    "-subj", "/CN=test"], check=True, capture_output=True)
    return priv, pub


@pytest.fixture
def sources(tmp_path):
    root = tmp_path / "app"
    (root / "data" / "uploads").mkdir(parents=True)
    (root / "data" / "uploads" / "stock.png").write_bytes(b"an upload")
    (root / "labels").mkdir()
    (root / "labels" / "sheet.pdf").write_bytes(b"a label")
    (root / "data" / "bloomington.csv").write_text("stock,genotype\n")
    return root


def _run(tmp_path, sources, pub, env=None):
    out = tmp_path / "out"
    return build_state_backup(
        destination=out, public_key=pub,
        sources=[sources / "data" / "uploads", sources / "labels",
                 sources / "data" / "bloomington.csv"],
        environ=env if env is not None else {"SECRET_KEY": "s3cret", "PATH": "/bin"},
    )


def test_it_writes_an_archive_and_an_encrypted_environment(tmp_path, sources, keypair):
    _, pub = keypair
    result = _run(tmp_path, sources, pub)
    assert result["archive"].exists() and result["environment"].exists()
    assert result["archive"].name.startswith("flymanager_state_")
    assert result["environment"].name.endswith(".env.enc")


def test_the_archive_contains_every_source(tmp_path, sources, keypair):
    _, pub = keypair
    result = _run(tmp_path, sources, pub)
    with tarfile.open(result["archive"]) as tar:
        names = tar.getnames()
    assert any(n.endswith("stock.png") for n in names), names
    assert any(n.endswith("sheet.pdf") for n in names), names
    assert any(n.endswith("bloomington.csv") for n in names), names


def test_the_environment_decrypts_back_to_what_went_in(tmp_path, sources, keypair):
    """The whole point: these values exist nowhere else, so a backup that
    cannot be decrypted is not a backup."""
    priv, pub = keypair
    env = {"SECRET_KEY": "0" * 64, "SMTP_PASSWORD": "hunter2", "PATH": "/bin"}
    result = _run(tmp_path, sources, pub, env=env)
    plain = subprocess.run(
        ["openssl", "smime", "-decrypt", "-binary", "-inform", "DEM",
         "-in", str(result["environment"]), "-inkey", str(priv)],
        check=True, capture_output=True).stdout.decode()
    recovered = dict(line.split("=", 1) for line in plain.splitlines() if "=" in line)
    assert recovered == env


def test_the_environment_file_is_not_readable_as_plaintext(tmp_path, sources, keypair):
    _, pub = keypair
    result = _run(tmp_path, sources, pub, env={"SECRET_KEY": "MAGICVALUE"})
    assert b"MAGICVALUE" not in result["environment"].read_bytes()
    assert oct(result["environment"].stat().st_mode)[-3:] == "600"


def test_checksums_are_written_by_bare_filename(tmp_path, sources, keypair):
    """An absolute path in the .sha256 is verifiable only on the machine that
    wrote it -- never after pulling the pair back from off-site."""
    _, pub = keypair
    result = _run(tmp_path, sources, pub)
    for key in ("archive", "environment"):
        line = Path(str(result[key]) + ".sha256").read_text().strip()
        assert line.endswith(result[key].name)
        assert "/" not in line.split()[-1], line


def test_a_missing_public_key_fails_loudly_rather_than_writing_plaintext(tmp_path, sources):
    """Silently degrading to an unencrypted environment dump would be worse
    than not running at all."""
    with pytest.raises(FileNotFoundError):
        _run(tmp_path, sources, tmp_path / "absent.pem")
    assert not list((tmp_path / "out").glob("*.env*")) if (tmp_path / "out").exists() else True


def test_a_missing_source_is_skipped_not_fatal(tmp_path, sources, keypair):
    """A fresh install has no uploads yet; that must not stop the backup."""
    _, pub = keypair
    out = tmp_path / "out"
    result = build_state_backup(
        destination=out, public_key=pub,
        sources=[sources / "data" / "uploads", sources / "does-not-exist"],
        environ={"PATH": "/bin"},
    )
    assert result["archive"].exists()


def test_the_default_sources_are_the_paths_the_container_actually_mounts():
    assert [str(p) for p in STATE_BACKUP_SOURCES] == [
        "/app/data/uploads",
        "/app/flymanager/app/static/generated_labels",
        "/app/data/bloomington.csv",
    ]
