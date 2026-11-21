"""Non-destructive local backup rehearsal: pg_dump -> new DB -> checksum audit."""
import hashlib
import json
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path


def main():
    started = time.monotonic()
    suffix = uuid.uuid4().hex[:12]
    name = "kienchung_restore_" + suffix
    destination = Path(".local/backups") / suffix
    destination.mkdir(parents=True, exist_ok=False)
    compose = ["docker", "compose", "-f", "infra/compose.yaml", "-f", "infra/compose.ml.yaml", "exec", "-T"]
    def run(service, *args, stdin=None):
        process = subprocess.run([*compose, service, *args], input=stdin, capture_output=True, check=False)
        if process.returncode:
            # Never dump DB contents, cookies, credentials or provider errors into logs.
            raise RuntimeError(f"BACKUP_COMMAND_FAILED:{service}:{args[0]}:{process.returncode}")
        return process.stdout
    original_db = "kienchung"
    archive = run("postgres", "pg_dump", "-U", "kienchung", "-d", original_db, "--format=custom", "--no-owner", "--no-privileges")
    (destination / "database.dump").write_bytes(archive)
    created = False
    try:
        run("postgres", "createdb", "-U", "kienchung", name)
        created = True
        run("postgres", "pg_restore", "-U", "kienchung", "-d", name, "--no-owner", "--no-privileges", "--exit-on-error", stdin=archive)
        audit = json.loads(run("api", "python", "-m", "scripts.verify_restored_data", "--db-name", name, "--folder", destination.as_posix()))
    finally:
        if created:
            # Only the exact database created successfully above can be dropped.
            assert name.startswith("kienchung_restore_") and name != original_db
            run("postgres", "dropdb", "-U", "kienchung", name)
    result = {**audit, "scope": "LOCAL_POSTGRES_RESTORE_AND_ORIGINAL_EVIDENCE_COPY", "original_database_modified": False,
              "temporary_database_dropped": True, "backup_folder": destination.as_posix(), "database_dump_sha256": hashlib.sha256(archive).hexdigest(),
              "dump_bytes": len(archive), "seconds": time.monotonic()-started, "completed_at": datetime.now(UTC).isoformat(),
              "limitations": ["Same-machine recovery exercise, not an offsite backup or a full public-deployment recovery test.", "No secrets or backups included in release artifacts."]}
    Path("artifacts/evaluation/backup-restore.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
