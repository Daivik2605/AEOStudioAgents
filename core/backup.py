"""`aeo backup` (PLAN.md step 17a, local only): one dated folder holding everything that cannot be rebuilt.

    <dest>/2026-10-31_1405_aeo_backup/
        database.dump     a full dump of the database (pg_dump custom format, restored with pg_restore)
        evidence.tar.gz   every clients/*/evidence/ folder, each client's notes files, and question_sets/ if it exists
                          (--include-generated adds clients/*/generated/, which can be rebuilt)
        manifest.json     date, tool version, database name, rows per table, sizes, a SHA-256 for each file
        RESTORE.md        plain steps to restore by hand, written from the same commands this code runs

Rules that hold throughout:
  * Nothing secret goes into the folder: not the database address, not the password, no .env.
    The password is handed to the database tools through the environment, never on a command line.
  * The folder is built under a hidden temporary name and renamed into place last, so a failed run never leaves
    something that looks like a real backup. An earlier backup is never overwritten or deleted.
  * `--verify` restores into a scratch database it creates and always drops. It never touches the dev or test database.
  * There is no prune and no restore command. Restoring is a manual, human step.

Which database tools: pg_dump and pg_restore on this machine if their version is at least the server's (a newer server
refuses an older pg_dump); otherwise the same tools inside the database's Docker container.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

REPO = Path(__file__).parent.parent
COMPOSE_FILE = REPO / "docker-compose.yml"            # kept apart from REPO so a test can point REPO elsewhere
FOLDER_SUFFIX = "_aeo_backup"
FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_aeo_backup(?:_\d+)?$")
DUMP, ARCHIVE, MANIFEST, RESTORE = "database.dump", "evidence.tar.gz", "manifest.json", "RESTORE.md"
HASHED = (DUMP, ARCHIVE, RESTORE)                  # the manifest cannot hold its own checksum
SCRATCH_PREFIX = "aeo_verify_"
NOTE = "This backup contains client data. Keep it out of shared or public folders."
SKIP_NAMES = {".DS_Store"}


class BackupError(Exception):
    """The backup or check cannot go ahead. The message says what to do. Never a traceback."""


# ---- talking to the database ---------------------------------------------------------------------------------------------------

@dataclass
class Conn:
    host: str
    port: str
    user: str
    password: str | None
    dbname: str

    @classmethod
    def from_url(cls, url: str) -> "Conn":
        d = conninfo_to_dict(url)
        return cls(d.get("host") or "localhost", str(d.get("port") or "5432"), d.get("user") or "postgres",
                   d.get("password"), d.get("dbname") or "postgres")

    def url(self, dbname: str | None = None) -> str:
        return psycopg.conninfo.make_conninfo(host=self.host, port=self.port, user=self.user, password=self.password,
                                              dbname=dbname or self.dbname)

    def connect(self, dbname: str | None = None, **kw) -> psycopg.Connection:
        try:
            return psycopg.connect(self.url(dbname), connect_timeout=8, **kw)
        except psycopg.OperationalError as exc:
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else "no answer"
            raise BackupError(f"Cannot reach the database on {self.host}:{self.port} ({_scrub(first, self)}). "
                              "Is Docker running? Try `docker compose up -d`, then run this again.") from None


def _scrub(text: str, conn: Conn) -> str:
    return text.replace(conn.password, "***") if conn.password else text


def _major(version_text: str) -> int | None:
    m = re.search(r"(\d+)(?:\.\d+)?", version_text)
    return int(m.group(1)) if m else None


@dataclass
class Route:
    """How the database tools are run. `kind` is "local tools" or "container"."""
    kind: str
    conn: Conn
    container: str | None = None

    @property
    def label(self) -> str:
        return "local tools (pg_dump and pg_restore on this machine)" if self.kind == "local tools" \
            else f"Docker container {self.container} (pg_dump and pg_restore inside it)"

    def _command(self, tool: str, args: list[str]) -> list[str]:
        if self.kind == "local tools":
            return [tool, "-h", self.conn.host, "-p", self.conn.port, "-U", self.conn.user, *args]
        return ["docker", "exec", "-i", "-e", "PGPASSWORD", self.container, tool, "-U", self.conn.user, *args]

    def text(self, tool: str, args: list[str]) -> str:
        """The command as a person would type it. No password: the tools ask for it (or use the container)."""
        return " ".join(self._command(tool, args)).replace("-e PGPASSWORD ", "")

    def run(self, tool: str, args: list[str], *, stdin=None, stdout=subprocess.PIPE) -> subprocess.CompletedProcess:
        env = {**os.environ, "PGPASSWORD": self.conn.password or ""}
        try:
            return subprocess.run(self._command(tool, args), stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, env=env,
                                  timeout=1800)
        except FileNotFoundError as exc:
            raise BackupError(f"Could not run {exc.filename}: it is not installed.") from None
        except subprocess.TimeoutExpired:
            raise BackupError(f"{tool} took longer than 30 minutes and was stopped.") from None


def _container_name() -> str:
    """The database container, found from docker-compose.yml (service `postgres`). Clear errors, no traceback."""
    try:
        got = subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), "ps", "-q", "postgres"],
                             capture_output=True, text=True, timeout=30, cwd=COMPOSE_FILE.parent)
    except FileNotFoundError:
        raise BackupError("Docker is not installed or not on the PATH, and the PostgreSQL tools on this machine are "
                          "too old for the database. Install Docker, or install PostgreSQL 16 tools.") from None
    except subprocess.TimeoutExpired:
        raise BackupError("Docker did not answer in 30 seconds. Is Docker Desktop running?") from None
    ident = got.stdout.strip().splitlines()[0] if got.stdout.strip() else ""
    if got.returncode != 0 or not ident:
        raise BackupError("The database container is not running (or Docker is down). Start it with `docker compose up -d`.")
    named = subprocess.run(["docker", "inspect", "-f", "{{.Name}}", ident], capture_output=True, text=True, timeout=30)
    return named.stdout.strip().lstrip("/") or ident


def choose_route(conn: Conn, server_major: int, *, which=shutil.which) -> Route:
    """Local tools if both exist and are at least as new as the server; otherwise the container."""
    if which("pg_dump") and which("pg_restore"):
        try:
            out = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        if (_major(out) or 0) >= server_major:
            return Route("local tools", conn)
    if conn.host not in ("localhost", "127.0.0.1", "::1"):
        raise BackupError(f"The PostgreSQL tools on this machine are older than the server (version {server_major}), and the "
                          "server is not on this machine, so the Docker container cannot stand in. Install PostgreSQL "
                          f"{server_major} client tools.")
    return Route("container", conn, _container_name())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tool_version() -> str:
    try:
        return metadata.version("aeo-studio-agents")
    except metadata.PackageNotFoundError:
        return "unknown"


# ---- the archive -----------------------------------------------------------------------------------------------------------------

def archive_members(clients_root: Path, repo_root: Path, include_generated: bool) -> list[tuple[Path, str]]:
    """(file, name inside the archive), sorted. Evidence folders, every client's own notes files, question_sets/, and
    with include_generated the generated folders. No links, no .env files, ever."""
    found: list[tuple[Path, str]] = []

    def take(path: Path, arc: str) -> None:
        if path.is_symlink() or not path.is_file() or path.name in SKIP_NAMES or path.name.startswith(".env"):
            return
        found.append((path, arc))

    def take_tree(folder: Path, arc: str) -> None:
        for path in sorted(folder.rglob("*")):
            take(path, f"{arc}/{path.relative_to(folder).as_posix()}")

    if clients_root.is_dir():
        for client in sorted(p for p in clients_root.iterdir() if p.is_dir() and not p.is_symlink()):
            for path in sorted(client.iterdir()):                       # notes: timeline.md, work_log.md, drafts, ...
                take(path, f"clients/{client.name}/{path.name}")
            take_tree(client / "evidence", f"clients/{client.name}/evidence")
            if include_generated:
                take_tree(client / "generated", f"clients/{client.name}/generated")
    take_tree(repo_root / "question_sets", "question_sets")              # frozen question sets, if the folder exists
    return found


def write_archive(path: Path, members: list[tuple[Path, str]]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for file, arc in members:
            tar.add(file, arcname=arc, recursive=False)


# ---- making a backup --------------------------------------------------------------------------------------------------------------

@dataclass
class BackupResult:
    folder: Path
    route: Route
    seconds: float
    bytes: int
    tables: dict[str, int]
    archive_files: int
    include_generated: bool


def _tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                        "AND table_type = 'BASE TABLE' ORDER BY table_name").fetchall()
    return [r[0] for r in rows]


def _free_name(dest: Path, stamp: str) -> str:
    n = 1
    while True:
        name = f"{stamp}{FOLDER_SUFFIX}" + ("" if n == 1 else f"_{n}")
        if not (dest / name).exists():
            return name
        n += 1


def make_backup(database_url: str, dest: Path, *, include_generated: bool = False, clients_root: Path | None = None,
                repo_root: Path | None = None, now: datetime | None = None, route: Route | None = None) -> BackupResult:
    started = time.monotonic()
    dest, repo_root = Path(dest).expanduser().resolve(), Path(repo_root or REPO).resolve()
    clients_root = Path(clients_root) if clients_root is not None else repo_root / "clients"
    if dest == repo_root or repo_root in dest.parents:
        raise BackupError(f"The backup folder {dest} is inside the project. Choose a folder outside it (the default is "
                          "~/aeo-backups), so a backup can never be committed.")
    conn = Conn.from_url(database_url)
    probe = conn.connect()                                                  # one clear error if the database is down
    try:
        major = int(probe.execute("SHOW server_version_num").fetchone()[0]) // 10000
    finally:
        probe.close()
    route = route or choose_route(conn, major)

    dest.mkdir(parents=True, exist_ok=True)
    temp = dest / f".tmp_{secrets.token_hex(6)}_aeo_backup"
    temp.mkdir()
    try:
        # One snapshot for both the dump and the row counts, so the manifest describes exactly what was dumped.
        snap = conn.connect()
        try:
            snap.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            snapshot_id = snap.execute("SELECT pg_export_snapshot()").fetchone()[0]
            names = _tables(snap)
            tables = {t: snap.execute(psycopg.sql.SQL("SELECT count(*) FROM public.{}").format(psycopg.sql.Identifier(t))).fetchone()[0]
                      for t in names}
            with open(temp / DUMP, "wb") as out:
                done = route.run("pg_dump", ["-Fc", f"--snapshot={snapshot_id}", "-d", conn.dbname], stdout=out)
            if done.returncode != 0:
                raise BackupError("pg_dump failed: " + _scrub(done.stderr.decode(errors="replace").strip()[:500], conn))
        finally:
            snap.close()

        members = archive_members(clients_root, repo_root, include_generated)
        write_archive(temp / ARCHIVE, members)
        (temp / RESTORE).write_text(restore_text(route, conn.dbname, include_generated), encoding="utf-8")

        stamp = (now or datetime.now(timezone.utc).astimezone()).strftime("%Y-%m-%d_%H%M")
        files = {n: {"sha256": sha256(temp / n), "bytes": (temp / n).stat().st_size} for n in HASHED}
        manifest = {
            "date": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds"),
            "tool_version": tool_version(), "database": conn.dbname, "route": route.kind,
            "include_generated": include_generated, "tables": tables, "archive_files": len(members),
            "file_count": len(HASHED) + 1, "total_bytes": sum(f["bytes"] for f in files.values()),
            "files": files, "last_verified": None,
            "notes": "total_bytes and files cover database.dump, evidence.tar.gz and RESTORE.md; this manifest cannot "
                     "hold its own checksum. last_verified is the only field that changes after the backup is made.",
        }
        (temp / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        final = None
        for _ in range(50):                                                  # never onto an existing folder
            name = _free_name(dest, stamp)
            try:
                if (dest / name).exists():
                    continue
                os.rename(temp, dest / name)
                final = dest / name
                break
            except OSError:
                continue
        if final is None:
            raise BackupError("Could not find a free backup folder name.")
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)                              # our own unfinished folder, never a real backup
        raise
    total = sum(f["bytes"] for f in files.values()) + (final / MANIFEST).stat().st_size
    return BackupResult(final, route, time.monotonic() - started, total, tables, len(members), include_generated)


def restore_text(route: Route, dbname: str, include_generated: bool) -> str:
    new = "aeo_restored"
    create = route.text("psql", ["-d", "postgres", "-c", f'"CREATE DATABASE {new}"'])
    restore = route.text("pg_restore", ["--no-owner", "-d", new])
    feed = "< database.dump"
    return f"""# How to restore this backup

{NOTE}

This is done by hand, on purpose. Nothing here restores itself, and no command in this tool does it for you.
Restore into a NEW, empty database first and look at it. Do not restore over a database you are using.

The backup was made from the database `{dbname}` with: {route.label}.

## 1. Check the files are intact

    aeo backup --verify <this folder>

## 2. Put the database back (into a new database called {new})

Run these from inside this folder. If asked for a password, it is the database password you use for this project.

    {create}
    {restore} {feed}

To check it, count a table, for example: {route.text("psql", ["-d", new, "-c", '"SELECT count(*) FROM businesses"'])}

When you are happy, point the project's DATABASE_URL setting at `{new}` (or dump and reload it into the database you want).

## 3. Put the files back

The archive keeps the project's own folder layout (clients/<domain>/evidence/, the notes files{", clients/<domain>/generated/" if include_generated else ""}, question_sets/).
Unpack it into a spare folder first, look, then copy what you want into the project:

    mkdir restored_files && tar -xzf evidence.tar.gz -C restored_files

`manifest.json` lists the tables with their row counts and a SHA-256 for each file.
"""


# ---- reading backups (list, verify) ---------------------------------------------------------------------------------------------

def read_manifest(folder: Path) -> dict:
    path = Path(folder) / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BackupError(f"{path} is missing: this is not a complete backup.") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupError(f"{path} cannot be read ({exc}).") from None
    if not isinstance(data, dict) or not all(k in data for k in ("tables", "files", "archive_files", "database")):
        raise BackupError(f"{path} does not look like a backup manifest.")
    return data


def list_backups(dest: Path) -> list[dict]:
    dest = Path(dest).expanduser()
    out = []
    for folder in sorted(p for p in (dest.iterdir() if dest.is_dir() else []) if p.is_dir() and FOLDER_RE.match(p.name)):
        row = {"name": folder.name, "path": folder, "bytes": sum(f.stat().st_size for f in folder.iterdir() if f.is_file()),
               "date": None, "verified": None, "problem": None}
        try:
            m = read_manifest(folder)
            row["date"] = m.get("date")
            lv = m.get("last_verified")
            row["verified"] = lv
        except BackupError as exc:
            row["problem"] = str(exc)
        out.append(row)
    return out


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class VerifyResult:
    checks: list[Check] = field(default_factory=list)
    scratch: str | None = None

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))


def verify_backup(folder: Path, server_url: str, *, route: Route | None = None) -> VerifyResult:
    """Checks a backup without touching real data. `server_url` only says which database SERVER to make the scratch
    database on; its own database is never opened for anything but finding the server's version."""
    folder = Path(folder).expanduser()
    result = VerifyResult()
    if not folder.is_dir():
        raise BackupError(f"{folder} is not a folder.")
    try:
        manifest = read_manifest(folder)
    except BackupError as exc:
        result.add("manifest", False, str(exc))
        return result
    result.add("manifest", True, f"{manifest['database']}, made {manifest.get('date', 'at an unknown time')}")

    for name in HASHED:
        path, want = folder / name, manifest["files"].get(name, {}).get("sha256")
        if not path.is_file():
            result.add(f"checksum {name}", False, "the file is missing")
        elif want is None:
            result.add(f"checksum {name}", False, "the manifest has no checksum for it")
        else:
            got = sha256(path)
            result.add(f"checksum {name}", got == want, "matches the manifest" if got == want else "does not match the manifest")

    try:
        with tarfile.open(folder / ARCHIVE, "r:gz") as tar:
            count = sum(1 for m in tar if m.isfile())
        result.add("archive opens", count == manifest["archive_files"], f"{count} file(s)" if count == manifest["archive_files"]
                   else f"holds {count} file(s) but the manifest says {manifest['archive_files']}")
    except (OSError, tarfile.TarError, EOFError) as exc:
        result.add("archive opens", False, f"cannot be read ({exc})")

    server = Conn.from_url(server_url)
    scratch = f"{SCRATCH_PREFIX}{secrets.token_hex(6)}"
    if scratch == server.dbname or not scratch.startswith(SCRATCH_PREFIX):
        raise BackupError("Refusing to use that name for the scratch database.")      # cannot happen; a guard on purpose
    admin = server.connect("postgres", autocommit=True)
    try:
        route = route or choose_route(server, int(admin.execute("SHOW server_version_num").fetchone()[0]) // 10000)
        result.scratch = scratch
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(scratch)))
        try:
            dump = folder / DUMP
            if not dump.is_file():
                result.add("database restores", False, "database.dump is missing")
            else:
                with open(dump, "rb") as f:
                    done = route.run("pg_restore", ["--no-owner", "--exit-on-error", "-d", scratch], stdin=f)
                if done.returncode != 0:
                    err = _scrub(done.stderr.decode(errors="replace").strip().splitlines()[-1:][0] if done.stderr.strip() else "", server)
                    result.add("database restores", False, f"pg_restore failed: {err[:300]}")
                else:
                    result.add("database restores", True, f"into scratch database {scratch}")
                    result.checks.extend(_compare_counts(server, scratch, manifest["tables"]))
        finally:
            try:
                admin.execute(psycopg.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(psycopg.sql.Identifier(scratch)))
                result.add("scratch database dropped", True, scratch)
            except psycopg.Error as exc:
                result.add("scratch database dropped", False, f"{scratch} is still there; drop it by hand ({exc})")
    finally:
        admin.close()
    _record_verification(folder, result)
    return result


def _compare_counts(server: Conn, scratch: str, expected: dict[str, int]) -> list[Check]:
    with server.connect(scratch) as c:
        have = {t: c.execute(psycopg.sql.SQL("SELECT count(*) FROM public.{}").format(psycopg.sql.Identifier(t))).fetchone()[0]
                for t in _tables(c)}
    wrong = [f"{t}: manifest {n}, restored {have.get(t, 'missing')}" for t, n in expected.items() if have.get(t) != n]
    extra = [t for t in have if t not in expected]
    wrong += [f"{t}: restored but not in the manifest" for t in extra]
    return [Check("row counts match the manifest", not wrong,
                  f"{len(expected)} tables, {sum(expected.values())} rows" if not wrong else "; ".join(wrong[:5]))]


def _record_verification(folder: Path, result: VerifyResult) -> None:
    """Writes last_verified into the manifest (and nothing else in the backup). Skipped if the manifest cannot be rewritten."""
    path = folder / MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["last_verified"] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "passed": result.passed}
        temp = path.with_name(MANIFEST + ".tmp")
        temp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except (OSError, ValueError):
        pass
