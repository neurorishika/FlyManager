"""Backup retention: the selection rule, and the embedded copies of it.

Flat "delete older than N days" retention cannot bound storage, and once
backups start failing silently it empties the directory outright -- a silent
failure becoming total loss. Grandfather-father-son keeps a bounded set
forever. This is deletion logic, so it gets tested like deletion logic.
"""
import datetime
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
AWK = REPO / "scripts" / "lib" / "gfs-select.awk"
SCRIPTS = [REPO / "scripts" / "container-mongo-backup.sh",
           REPO / "scripts" / "nas-state-backup.sh"]
TODAY = datetime.date(2026, 9, 2)


def _select(names, today=TODAY, min_keep=3):
    """Names the selector marks for deletion."""
    out = subprocess.run(["awk", "-f", str(AWK), "-v", f"TODAY={today:%Y%m%d}",
                          "-v", f"MIN_KEEP={min_keep}"],
                         input="\n".join(names), capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return [line for line in out.stdout.splitlines() if line]


def _series(days, per_day=4, prefix="flymanager_mongodb_"):
    names = []
    for d in range(days):
        day = TODAY - datetime.timedelta(days=d)
        for h in range(0, 24, 24 // per_day):
            names.append(f"{prefix}{day:%Y%m%d}T{h:02d}0000Z.archive.gz")
    return names


def test_two_years_of_six_hourly_backups_stays_bounded():
    names = _series(730)
    kept = set(names) - set(_select(names))
    assert len(names) > 2900
    assert 60 <= len(kept) <= 130, f"kept {len(kept)}"


def test_the_newest_archive_is_never_deleted():
    names = _series(400)
    assert max(names) not in _select(names)


def test_recent_archives_are_all_kept():
    names = _series(30)
    deleted = _select(names)
    for name in names:
        stamp = re.search(r"(\d{8})T", name).group(1)
        age = (TODAY - datetime.datetime.strptime(stamp, "%Y%m%d").date()).days
        if age <= 7:
            assert name not in deleted, f"deleted a {age}-day-old archive"


def test_the_daily_tier_keeps_exactly_one_per_day():
    names = _series(30)
    kept = set(names) - set(_select(names))
    per_day = {}
    for name in kept:
        stamp = re.search(r"(\d{8})T", name).group(1)
        age = (TODAY - datetime.datetime.strptime(stamp, "%Y%m%d").date()).days
        if 7 < age <= 30:
            per_day[stamp] = per_day.get(stamp, 0) + 1
    assert per_day and all(v == 1 for v in per_day.values()), per_day


def test_a_stalled_backup_set_is_never_emptied():
    """Every archive years old and no new ones arriving: the floor holds.
    This is the failure mode that turns a silent outage into total loss."""
    names = [f"flymanager_mongodb_2019010{i}T000000Z.archive.gz" for i in range(1, 4)]
    assert _select(names) == []


def test_files_without_a_timestamp_are_never_touched():
    names = ["README", "notes.txt", "rclone.conf",
             "flymanager_mongodb_20190101T000000Z.archive.gz"]
    deleted = _select(names, min_keep=0)
    assert "README" not in deleted and "notes.txt" not in deleted
    assert "rclone.conf" not in deleted


def test_prune_on_disk_removes_each_archive_with_its_checksum(tmp_path):
    """The selector works on archive names; the shell wrapper must take the
    .sha256 along, or the directory fills with orphaned checksums."""
    script = tmp_path / "prune.sh"
    body = SCRIPTS[0].read_text()
    start = body.index("# --- BEGIN EMBEDDED")
    end = body.index("# --- END EMBEDDED") + len("# --- END EMBEDDED gfs-select.awk ---")
    script.write_text("#!/bin/sh\nset -eu\n" + body[start:end] +
                      '\ngfs_prune_local "$1" "flymanager_mongodb_*.archive.gz" 3\n')
    script.chmod(0o755)

    for name in _series(400):
        (tmp_path / name).write_text("x")
        (tmp_path / (name + ".sha256")).write_text("x")
    before = len(list(tmp_path.glob("flymanager_mongodb_*.archive.gz")))

    subprocess.run(["sh", str(script), str(tmp_path)], check=True,
                   capture_output=True, text=True)

    archives = list(tmp_path.glob("flymanager_mongodb_*.archive.gz"))
    sums = list(tmp_path.glob("flymanager_mongodb_*.archive.gz.sha256"))
    assert len(archives) < before, "nothing was pruned"
    assert len(archives) == len(sums), "checksums left orphaned"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_the_embedded_copy_matches_the_canonical_awk(script):
    """The backup container bind-mounts only its own script, so the awk has to
    be embedded. That makes drift possible, so it is asserted instead."""
    body = script.read_text()
    embedded = body[body.index("cat <<'GFSAWK'\n") + len("cat <<'GFSAWK'\n"):body.index("GFSAWK\n}")]
    assert embedded == AWK.read_text(), f"{script.name} has drifted from {AWK.name}"


def test_awk_is_available():
    assert shutil.which("awk"), "these scripts run wherever awk does"
