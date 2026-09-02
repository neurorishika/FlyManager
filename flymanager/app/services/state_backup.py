"""Back up the state that mongodump does not cover, from inside the app.

`mongodump` captures every collection and GridFS blob, so records and marker
images are already safe. Two things sit outside it:

- the files on disk: uploads, generated labels, the Bloomington CSV;
- the process environment, which is the only place SECRET_KEY, the admin
  credentials and the SMTP credentials exist. Losing SECRET_KEY invalidates
  every session; losing the admin password locks the lab out of its own
  instance.

This ran as a DSM Task Scheduler entry, which is a dependency living outside
the stack -- invisible in the compose file and easy to lose in a migration.
It now runs on the app's own APScheduler under the same Mongo-backed
distributed lock as the flip reminders.

Deliberately narrow: this module PRODUCES archives and nothing else. Pruning
and the off-site push stay in the backup container, which already owns the
GFS retention rule and the rclone configuration. One implementation of the
deletion logic is worth more than the convenience of doing it here.
"""
import os
import subprocess
import tarfile
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

# Where the app container actually mounts the things worth keeping. The labels
# path is not under /app/data -- it is served as a static directory -- which is
# why these are listed explicitly rather than globbed from one root.
STATE_BACKUP_SOURCES = (
    Path("/app/data/uploads"),
    Path("/app/flymanager/app/static/generated_labels"),
    Path("/app/data/bloomington.csv"),
)
DEFAULT_DESTINATION = Path("/backups/state")
DEFAULT_PUBLIC_KEY = Path("/backups/state-backup-public.pem")


def _write_checksum(path):
    """Checksum by BARE FILENAME.

    sha256sum-compatible output recording an absolute path is verifiable only
    on the machine that wrote it, never after the pair is pulled back from
    off-site -- the one occasion it exists for.
    """
    digest = sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def build_state_backup(destination=None, public_key=None, sources=None, environ=None):
    """Write a state archive and an encrypted environment dump.

    Returns {"archive": Path, "environment": Path}. Raises rather than
    degrading: an unencrypted environment dump would be worse than no backup,
    and a silent failure here is indistinguishable from success until the day
    it matters.
    """
    destination = Path(destination or DEFAULT_DESTINATION)
    public_key = Path(public_key or DEFAULT_PUBLIC_KEY)
    sources = [Path(p) for p in (sources if sources is not None else STATE_BACKUP_SOURCES)]
    environ = os.environ if environ is None else environ

    if not public_key.is_file():
        raise FileNotFoundError(
            f"No public key at {public_key}; refusing to write the environment "
            "in plaintext."
        )

    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # --- files -------------------------------------------------------------
    archive = destination / f"flymanager_state_{stamp}.tar.gz"
    tmp_archive = archive.with_suffix(archive.suffix + ".tmp")
    try:
        with tarfile.open(tmp_archive, "w:gz") as tar:
            for source in sources:
                # A fresh install has no uploads yet; that is not a failure.
                if source.exists():
                    tar.add(source, arcname=source.name)
        tmp_archive.replace(archive)
    finally:
        if tmp_archive.exists():
            tmp_archive.unlink()
    _write_checksum(archive)

    # --- environment -------------------------------------------------------
    # openssl rather than a pure-Python hybrid scheme, so the output format is
    # identical to scripts/nas-state-backup.sh and one documented decrypt
    # command works for both. Encrypted to a PUBLIC key: the running app can
    # write these and cannot read them back, so neither a compromised
    # container nor a leaked off-site copy exposes the secrets.
    env_path = destination / f"flymanager_env_{stamp}.env.enc"
    payload = "".join(f"{key}={value}\n" for key, value in sorted(environ.items()))
    subprocess.run(
        ["openssl", "smime", "-encrypt", "-binary", "-aes-256-cbc",
         "-outform", "DEM", "-out", str(env_path), str(public_key)],
        input=payload.encode("utf-8"), check=True, capture_output=True,
    )
    env_path.chmod(0o600)
    _write_checksum(env_path)

    return {"archive": archive, "environment": env_path}


def run_state_backup(app):
    """Scheduler entry point, matching the other services' signature.

    Concurrency safety comes from the distributed lock in
    flymanager.app.run_locked_scheduled_job, which wraps every call site.
    """
    with app.app_context():
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            result = build_state_backup()
        except Exception as exc:
            # Logged loudly: a backup that stops running looks exactly like one
            # that runs fine, right up until a restore is needed.
            app.logger.error("[%s] State backup FAILED: %s", timestamp, exc)
            raise
        app.logger.info("[%s] State backup wrote %s and %s", timestamp,
                        result["archive"].name, result["environment"].name)
        return result
