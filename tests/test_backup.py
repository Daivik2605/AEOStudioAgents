"""`aeo backup` (PLAN.md step 17a). Every database here is the test database (or a scratch database the code makes and
drops on the same server); the dev database is never opened."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from aeo import cli
from core import backup as bk
from core.db import database_url

ROOT = Path(__file__).parent.parent


@pytest.fixture
def url():
    got = database_url()                                   # conftest has already pointed this at TEST_DATABASE_URL
    assert bk.Conn.from_url(got).dbname.endswith("_test")
    return got


@pytest.fixture
def project(tmp_path):
    """A fake project folder with two clients, so no real client file is ever read."""
    root = tmp_path / "project"
    for domain in ("a.example", "b.example"):
        c = root / "clients" / domain
        (c / "evidence").mkdir(parents=True)
        (c / "generated").mkdir()
        (c / "evidence" / "2026-10-01_page.html").write_text(f"<html>{domain}</html>")
        (c / "generated" / "2026-10-01_report.md").write_text("rebuildable")
        for n in ("timeline.md", "work_log.md", "dossier_notes.md", "question_set_draft_v1.yaml"):
            (c / n).write_text(f"{domain} {n}")
        (c / ".env").write_text("sk-ant-should-never-be-saved")
    (root / "question_sets").mkdir()
    (root / "question_sets" / "x_v1.yaml").write_text("questions: [a]")
    return root


@pytest.fixture
def dest(tmp_path):
    return tmp_path / "backups"


def make(url, dest, project, **kw):
    return bk.make_backup(url, dest, clients_root=project / "clients", repo_root=project, **kw)


def table_counts(url) -> dict[str, int]:
    with psycopg.connect(url) as c:
        names = [r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")]
        return {t: c.execute(psycopg.sql.SQL("SELECT count(*) FROM public.{}").format(psycopg.sql.Identifier(t))).fetchone()[0] for t in names}


def scratch_databases(url) -> list[str]:
    with psycopg.connect(bk.Conn.from_url(url).url("postgres"), autocommit=True) as c:
        return [r[0] for r in c.execute("SELECT datname FROM pg_database WHERE datname LIKE %s", (bk.SCRATCH_PREFIX + "%",))]


def names_in(folder: Path) -> set[str]:
    with tarfile.open(folder / bk.ARCHIVE) as t:
        return {m.name for m in t if m.isfile()}


# ---- making a backup ---------------------------------------------------------------------------------------------------------------

def test_a_backup_has_the_four_files_and_correct_counts_and_checksums(url, dest, project):
    r = make(url, dest, project)
    assert sorted(p.name for p in r.folder.iterdir()) == sorted([bk.DUMP, bk.ARCHIVE, bk.MANIFEST, bk.RESTORE])
    m = json.loads((r.folder / bk.MANIFEST).read_text())
    assert m["tables"] == table_counts(url) and m["database"].endswith("_test") and m["tool_version"] and m["date"]
    for name in bk.HASHED:
        assert m["files"][name]["sha256"] == hashlib.sha256((r.folder / name).read_bytes()).hexdigest()
        assert m["files"][name]["bytes"] == (r.folder / name).stat().st_size
    assert m["file_count"] == 4 and m["archive_files"] == len(names_in(r.folder)) and m["last_verified"] is None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}_aeo_backup", r.folder.name)
    assert [p.name for p in dest.iterdir()] == [r.folder.name]                      # no temporary folder left


def test_the_output_says_which_route_was_used(url, dest, project):
    r = make(url, dest, project)
    assert r.route.kind in ("local tools", "container") and ("local tools" in r.route.label or "Docker container" in r.route.label)
    assert r.route.kind in (json.loads((r.folder / bk.MANIFEST).read_text())["route"],)


# ---- what goes in the archive ----------------------------------------------------------------------------------------------------

def test_evidence_and_notes_are_included_and_generated_only_when_asked(url, dest, project):
    plain = names_in(make(url, dest, project).folder)
    assert "clients/a.example/evidence/2026-10-01_page.html" in plain and "clients/b.example/evidence/2026-10-01_page.html" in plain
    for n in ("timeline.md", "work_log.md", "dossier_notes.md", "question_set_draft_v1.yaml"):
        assert f"clients/a.example/{n}" in plain
    assert "question_sets/x_v1.yaml" in plain
    assert not any("/generated/" in n for n in plain)
    with_gen = names_in(make(url, dest, project, include_generated=True).folder)
    assert "clients/a.example/generated/2026-10-01_report.md" in with_gen and plain < with_gen


def test_env_files_are_never_archived(url, dest, project):
    assert not any(".env" in n for n in names_in(make(url, dest, project, include_generated=True).folder))


def test_no_secrets_anywhere_in_the_backup(url, dest, project):
    r = make(url, dest, project, include_generated=True)
    conn = bk.Conn.from_url(url)
    # In this setup the password is the word "postgres", which also legitimately appears as the user and owner name.
    # A password that is also a normal word can only be checked as part of an address; a distinctive one is checked bare.
    password = conn.password if conn.password not in (conn.user, "postgres") else None
    address = f"{conn.user}:{conn.password}@".encode()
    blobs = {p.name: p.read_bytes() for p in r.folder.iterdir()}
    with tarfile.open(r.folder / bk.ARCHIVE) as t:                                  # the archive is compressed: read inside it
        for m in t:
            if m.isfile():
                blobs["archive:" + m.name] = t.extractfile(m).read()
    sql = r.route.run("pg_restore", ["-f", "-"], stdin=open(r.folder / bk.DUMP, "rb")).stdout   # the dump as readable SQL
    blobs["dump as sql"] = sql
    for name, data in blobs.items():
        for needle in (b".env", b"sk-ant-", b"postgresql://", b"postgres://"):
            assert needle not in data, f"{needle!r} found in {name}"
        assert address not in data, f"a database address with its password is in {name}"
        if password:
            assert password.encode() not in data, f"the database password is in {name}"
    assert not any(p.name.startswith(".env") for p in r.folder.rglob("*"))


# ---- never overwrite, never leave half a backup ---------------------------------------------------------------------------------

def test_an_existing_folder_is_never_overwritten(url, dest, project):
    when = datetime(2026, 10, 31, 14, 5, tzinfo=timezone.utc)
    first = make(url, dest, project, now=when)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.folder.iterdir()}
    second = make(url, dest, project, now=when)
    third = make(url, dest, project, now=when)
    assert len({first.folder, second.folder, third.folder}) == 3 and second.folder.name.endswith("_aeo_backup_2")
    assert third.folder.name.endswith("_aeo_backup_3")
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.folder.iterdir()}


def test_a_failure_partway_leaves_no_folder(url, dest, project, monkeypatch):
    monkeypatch.setattr(bk, "write_archive", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        make(url, dest, project)
    assert list(dest.iterdir()) == []


def test_a_failed_dump_leaves_no_folder_and_a_clear_error(url, dest, project):
    wrong = bk.Conn.from_url(url)
    wrong.dbname = "no_such_database_here"
    with pytest.raises(bk.BackupError):
        bk.make_backup(wrong.url(), dest, clients_root=project / "clients", repo_root=project)
    assert not dest.exists() or list(dest.iterdir()) == []


def test_a_dump_that_fails_midway_leaves_no_folder(url, dest, project, monkeypatch):
    real = bk.Route.run
    monkeypatch.setattr(bk.Route, "run", lambda self, tool, args, **kw: subprocess.CompletedProcess([], 1, b"", b"pg_dump: boom"))
    with pytest.raises(bk.BackupError, match="pg_dump failed"):
        make(url, dest, project)
    assert list(dest.iterdir()) == []
    monkeypatch.setattr(bk.Route, "run", real)


def test_a_destination_inside_the_project_is_refused(url, project):
    with pytest.raises(bk.BackupError, match="inside the project"):
        make(url, project / "backups", project)


def test_an_unreachable_database_is_one_clear_error(dest, project):
    bad = bk.Conn("localhost", "1", "postgres", "secretpw", "aeo_test").url()
    with pytest.raises(bk.BackupError) as e:
        bk.make_backup(bad, dest, clients_root=project / "clients", repo_root=project)
    assert "Cannot reach the database" in str(e.value) and "secretpw" not in str(e.value) and "docker compose up" in str(e.value)


# ---- verify ----------------------------------------------------------------------------------------------------------------------------

def verified(url, folder):
    return bk.verify_backup(folder, url)


def failed_names(result):
    return {c.name for c in result.checks if not c.ok}


def test_verify_passes_on_a_good_backup_and_drops_the_scratch_database(url, dest, project):
    r = make(url, dest, project)
    res = verified(url, r.folder)
    assert res.passed and {c.name for c in res.checks} >= {"manifest", "checksum database.dump", "archive opens", "database restores",
                                                           "row counts match the manifest", "scratch database dropped"}
    assert scratch_databases(url) == []


def test_verify_writes_last_verified_and_changes_nothing_else(url, dest, project):
    r = make(url, dest, project)
    before = {n: hashlib.sha256((r.folder / n).read_bytes()).hexdigest() for n in (bk.DUMP, bk.ARCHIVE, bk.RESTORE)}
    verified(url, r.folder)
    m = json.loads((r.folder / bk.MANIFEST).read_text())
    assert m["last_verified"]["passed"] is True and m["last_verified"]["at"]
    assert before == {n: hashlib.sha256((r.folder / n).read_bytes()).hexdigest() for n in before}
    assert verified(url, r.folder).passed                                                  # the edited manifest is still valid


def test_verify_fails_when_the_dump_is_corrupted(url, dest, project):
    r = make(url, dest, project)
    data = bytearray((r.folder / bk.DUMP).read_bytes())
    for i in range(200, 600):
        data[i] ^= 0xFF
    (r.folder / bk.DUMP).write_bytes(bytes(data))
    res = verified(url, r.folder)
    assert not res.passed and "checksum database.dump" in failed_names(res)
    assert scratch_databases(url) == []
    assert json.loads((r.folder / bk.MANIFEST).read_text())["last_verified"]["passed"] is False


def test_verify_catches_a_corrupted_dump_even_if_its_checksum_was_made_to_match(url, dest, project):
    r = make(url, dest, project)
    (r.folder / bk.DUMP).write_bytes(b"PGDMP" + b"not a real dump" * 50)
    m = json.loads((r.folder / bk.MANIFEST).read_text())
    m["files"][bk.DUMP]["sha256"] = hashlib.sha256((r.folder / bk.DUMP).read_bytes()).hexdigest()
    (r.folder / bk.MANIFEST).write_text(json.dumps(m))
    res = verified(url, r.folder)
    assert failed_names(res) == {"database restores"} and scratch_databases(url) == []


@pytest.mark.parametrize("missing", [bk.DUMP, bk.ARCHIVE, bk.RESTORE])
def test_verify_fails_when_a_file_is_missing(url, dest, project, missing):
    r = make(url, dest, project)
    (r.folder / missing).unlink()
    res = verified(url, r.folder)
    assert not res.passed and f"checksum {missing}" in failed_names(res) and scratch_databases(url) == []


def test_verify_fails_when_the_manifest_is_missing(url, dest, project):
    r = make(url, dest, project)
    (r.folder / bk.MANIFEST).unlink()
    res = verified(url, r.folder)
    assert not res.passed and failed_names(res) == {"manifest"}


def test_verify_fails_when_a_checksum_was_changed(url, dest, project):
    r = make(url, dest, project)
    m = json.loads((r.folder / bk.MANIFEST).read_text())
    m["files"][bk.ARCHIVE]["sha256"] = "0" * 64
    (r.folder / bk.MANIFEST).write_text(json.dumps(m))
    assert failed_names(verified(url, r.folder)) == {f"checksum {bk.ARCHIVE}"}


def test_verify_fails_when_the_row_counts_disagree(url, dest, project):
    r = make(url, dest, project)
    m = json.loads((r.folder / bk.MANIFEST).read_text())
    table = next(iter(m["tables"]))
    m["tables"][table] += 7
    (r.folder / bk.MANIFEST).write_text(json.dumps(m))
    res = verified(url, r.folder)
    assert failed_names(res) == {"row counts match the manifest"} and table in next(c.detail for c in res.checks if not c.ok)


def test_verify_fails_when_the_archive_will_not_open(url, dest, project):
    r = make(url, dest, project)
    (r.folder / bk.ARCHIVE).write_bytes(b"this is not a tar file")
    m = json.loads((r.folder / bk.MANIFEST).read_text())
    m["files"][bk.ARCHIVE]["sha256"] = hashlib.sha256(b"this is not a tar file").hexdigest()
    (r.folder / bk.MANIFEST).write_text(json.dumps(m))
    assert failed_names(verified(url, r.folder)) == {"archive opens"}


def test_the_scratch_database_is_dropped_even_when_the_restore_blows_up(url, dest, project, monkeypatch):
    r = make(url, dest, project)
    seen = []
    real = bk.Route.run

    def boom(self, tool, args, **kw):
        if tool == "pg_restore":
            seen.append(args[-1])
            raise bk.BackupError("the tool vanished")
        return real(self, tool, args, **kw)
    monkeypatch.setattr(bk.Route, "run", boom)
    with pytest.raises(bk.BackupError):
        verified(url, r.folder)
    assert seen and seen[0].startswith(bk.SCRATCH_PREFIX) and scratch_databases(url) == []


def test_verify_never_uses_the_dev_or_test_database(url, dest, project, monkeypatch):
    r = make(url, dest, project)
    names = []
    real = bk.Route.run

    def spy(self, tool, args, **kw):
        names.append(args[args.index("-d") + 1])
        return real(self, tool, args, **kw)
    monkeypatch.setattr(bk.Route, "run", spy)
    verified(url, r.folder)
    assert names and all(n.startswith(bk.SCRATCH_PREFIX) for n in names)


# ---- listing ----------------------------------------------------------------------------------------------------------------------------

def test_list_shows_date_size_and_whether_a_verify_passed(url, dest, project):
    a = make(url, dest, project, now=datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc))
    b = make(url, dest, project, now=datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc))
    verified(url, a.folder)
    (dest / "notes.txt").write_text("not a backup")
    (dest / ".tmp_leftover_aeo_backup").mkdir()
    rows = {r["name"]: r for r in bk.list_backups(dest)}
    assert set(rows) == {a.folder.name, b.folder.name}
    assert rows[a.folder.name]["verified"]["passed"] is True and rows[b.folder.name]["verified"] is None
    assert rows[a.folder.name]["bytes"] > 0 and rows[a.folder.name]["date"]


# ---- the command --------------------------------------------------------------------------------------------------------------------------

def test_the_command_makes_lists_and_verifies(url, dest, project, monkeypatch):
    monkeypatch.setattr(bk, "REPO", project)
    out = CliRunner().invoke(cli.app, ["backup", "--to", str(dest)])
    assert out.exit_code == 0, out.output
    assert "Database tools used:" in out.output and "contains client data" in out.output and "Traceback" not in out.output
    folder = next(dest.iterdir())
    listed = CliRunner().invoke(cli.app, ["backup", "--list", "--to", str(dest)])
    assert folder.name in listed.output and "not verified" in listed.output
    ok = CliRunner().invoke(cli.app, ["backup", "--verify", str(folder)])
    assert ok.exit_code == 0 and "PASS  database restores" in ok.output and "Result: PASS" in ok.output
    assert "verify PASSED" in CliRunner().invoke(cli.app, ["backup", "--list", "--to", str(dest)]).output
    (folder / bk.DUMP).write_bytes(b"junk")
    bad = CliRunner().invoke(cli.app, ["backup", "--verify", str(folder)])
    assert bad.exit_code == 1 and "FAIL  checksum database.dump" in bad.output and "Result: FAIL" in bad.output


def test_the_command_gives_one_error_and_no_traceback_when_the_database_is_down(dest, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:pw@localhost:1/aeo")
    out = CliRunner().invoke(cli.app, ["backup", "--to", str(dest)])
    assert out.exit_code == 1 and "Cannot reach the database" in out.output and "Traceback" not in out.output
    assert "pw@" not in out.output


@pytest.mark.parametrize("flag", ["--prune", "--restore"])
def test_there_is_no_prune_and_no_restore(flag):
    assert CliRunner().invoke(cli.app, ["backup", flag]).exit_code == 2


def test_the_default_destination_is_outside_the_project():
    assert not (Path("~/aeo-backups").expanduser().resolve() == ROOT or ROOT in Path("~/aeo-backups").expanduser().resolve().parents)


# ---- the route --------------------------------------------------------------------------------------------------------------------------

def test_without_local_tools_the_container_is_used_and_named(url):
    try:
        route = bk.choose_route(bk.Conn.from_url(url), 16, which=lambda _: None)
    except bk.BackupError:
        pytest.skip("Docker is not running here")
    assert route.kind == "container" and route.container and "Docker container" in route.label
    assert "PGPASSWORD" not in route.text("pg_dump", ["-Fc"])


def test_with_no_local_tools_and_a_remote_server_the_error_is_clear():
    with pytest.raises(bk.BackupError, match="older than the server"):
        bk.choose_route(bk.Conn("db.example.com", "5432", "u", "p", "d"), 16, which=lambda _: None)


def test_a_full_backup_and_verify_work_through_the_container_route(url, dest, project):
    try:
        route = bk.choose_route(bk.Conn.from_url(url), 16, which=lambda _: None)
    except bk.BackupError:
        pytest.skip("Docker is not running here")
    r = make(url, dest, project, route=route)
    assert r.route.kind == "container" and bk.verify_backup(r.folder, url, route=route).passed


def test_restore_notes_use_the_commands_the_code_runs(url, dest, project):
    r = make(url, dest, project)
    text = (r.folder / bk.RESTORE).read_text()
    assert r.route.text("pg_restore", ["--no-owner", "-d", "aeo_restored"]) in text
    assert "contains client data" in text and "by hand" in text


def test_plan_marks_17a_done_and_moves_next_to_18():
    rows = {l.split("|")[1].strip(): l for l in (ROOT / "PLAN.md").read_text().splitlines() if l.startswith("| ") and l.count("|") >= 4}
    assert rows["17a"].rstrip().endswith("| ✅ Done |") and rows["18"].rstrip().endswith("| ⏭ Next |")
