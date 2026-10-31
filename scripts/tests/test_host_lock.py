"""Host lock, nightly backup and deploy behaviour, run against the real scripts.

The scripts run unmodified. `docker`, `ssh`, `git`, `df` and `top` are replaced by stand-ins on
PATH; `flock`, `gzip`, `find` and the shell are real. Each stand-in that represents disk-heavy
work records whether the host lock was held at that moment, so "the lock covers the build and
the recreate" is observed, not inferred from the script text.

Why these exist: docs/audits/2026-10-22-backup-build-lock-proposal.md (the 02:00 dump and a
frontend build filled the disk together and left a truncated backup).
"""
import os
import subprocess
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LOCK_LIB = REPO / "scripts" / "deploy_lock.sh"
BACKUP = REPO / "scripts" / "pg_backup.sh"
REMOTE = REPO / "scripts" / "deploy_remote.sh"
DEPLOY = REPO / "scripts" / "deploy.sh"
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
MARKER = "-- PostgreSQL database dump complete"

FAKES = {
    # `flock -n FILE true` opens a new descriptor, so it fails exactly when someone holds it.
    "_held": """
        if flock -n "$DEPLOY_LOCK_FILE" true 2>/dev/null; then echo 0; else echo 1; fi
    """,
    "docker": r"""
        held=$(_held)
        case "$*" in
          *pg_dump*)
            echo "dump held=$held" >>"$FAKE_LOG"
            sleep "${FAKE_DUMP_SLEEP:-0}"
            echo "CREATE TABLE t (id int);"
            case "${FAKE_DUMP_MODE:-ok}" in
              ok) printf -- '-- PostgreSQL database dump complete\n\n\\unrestrict x\n' ;;
              fail) exit 1 ;;
              nomarker) : ;;
            esac ;;
          *" build "*|build*)
            echo "build held=$held" >>"$FAKE_LOG"; sleep "${FAKE_STEP_SLEEP:-0}"
            [ "${FAKE_BUILD_FAIL:-0}" = 1 ] && { echo "build broke" >&2; exit 1; }
            echo "built" ;;
          *" up "*)
            echo "up held=$held" >>"$FAKE_LOG"; sleep "${FAKE_STEP_SLEEP:-0}"; echo "Started" ;;
          *"config --services"*)
            [ "${FAKE_CONFIG_FAIL:-0}" = 1 ] && exit 1
            printf 'signal-engine\npostgres\n' ;;
          *inspect*)
            [ "${FAKE_INSPECT_FAIL:-0}" = 1 ] && exit 1
            [ "${FAKE_HEALTH_STATE:-}" = missing ] && exit 1
            case "$*" in
              *State.Status*) echo "${FAKE_HEALTH_STATE:-running/healthy}" ;;
              *State.Running*) echo "${FAKE_RUNNING:-true}" ;;
            esac ;;
          *"--format {{.Status}}"*) echo "Up 1 minute (healthy)" ;;
          *"--format {{.Names}}"*) echo "stockai-x-1" ;;
          *prune*) echo "prune held=$held" >>"$FAKE_LOG"; echo "Total reclaimed space: 0B" ;;
          logs*) echo "logs held=$held" >>"$FAKE_LOG"; echo "2026-10-30T19:00:59Z QueuePool limit reached" ;;
          *"system df"*) echo "TYPE TOTAL" ;;
          *) echo "unexpected docker $*" >>"$FAKE_LOG" ;;
        esac
    """,
    "git": r"""
        echo "git $1 held=$(_held)" >>"$FAKE_LOG"; echo "Already up to date."
    """,
    # Last argument is the remote command; run it here, as the EC2 shell would.
    "ssh": r"""
        bash -c "${@: -1}"
    """,
    "df": r"""
        case "$*" in
          *-BG*) printf 'Avail\n%sG\n' "${FAKE_DF_GB:-100}" ;;
          *-B1*) printf 'Avail\n%s\n' "${FAKE_DF_BYTES:-100000000000}" ;;
          *) printf 'Filesystem Size Used Avail\n/dev/x 100G 50G 50G\n' ;;
        esac
    """,
    "top": r"""
        echo "%Cpu(s):  1.0 us,  0.5 sy,  0.0 ni, 98.0 id,  0.5 wa"
    """,
}


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in FAKES.items():
        f = bin_dir / name
        f.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
        f.chmod(0o755)
    backups = tmp_path / "backups"
    backups.mkdir()
    e = {k: v for k, v in os.environ.items() if not k.startswith("DEPLOY_LOCK_")}
    e.update(
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        DEPLOY_LOCK_FILE=str(tmp_path / "deploy.lock"),
        DEPLOY_STATUS_FILE=str(tmp_path / "deploy.status"),
        FAKE_LOG=str(tmp_path / "calls.log"),
        BACKUP_DIR=str(backups),
        BACKUP_MIN_FREE_BYTES="0",
        DEPLOY_SETTLE_S="0",
        REMOTE_DIR=str(REPO),
        EC2_KEY="/dev/null",
        SKIP_FRONTEND_TESTS="1",
        DEPLOY_LOG_DIR=str(tmp_path / "container-logs"),
    )
    (tmp_path / "calls.log").touch()
    return {"env": e, "tmp": tmp_path, "backups": backups,
            "log": tmp_path / "calls.log", "status": tmp_path / "deploy.status"}


def run(cmd, env, timeout=60, **kw):
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, **kw)


def lock_snippet(body):
    return ["bash", "-c", f". {LOCK_LIB}\n{body}"]


def start_holder(env, seconds, what="holder"):
    """A separate process that holds the lock for `seconds`."""
    p = subprocess.Popen(lock_snippet(f'deploy_lock_acquire "{what}" || exit 9; sleep {seconds}'),
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    lock = Path(env["DEPLOY_LOCK_FILE"])
    for _ in range(100):
        if lock.exists() and f"what={what}" in lock.read_text():
            return p
        time.sleep(0.05)
    p.kill()
    raise AssertionError("holder never acquired the lock")


def lock_is_free(env):
    return run(["flock", "-n", env["DEPLOY_LOCK_FILE"], "true"], env).returncode == 0


def calls(e):
    return e["log"].read_text().splitlines()


def good_backup(path, payload="CREATE TABLE t (id int);"):
    subprocess.run(f"printf '%s\\n{MARKER}\\n' '{payload}' | gzip > '{path}'", shell=True, check=True)


# ── the lock itself ────────────────────────────────────────────────────────────────────────

def test_second_deploy_refuses_while_held(env):
    holder = start_holder(env["env"], 5)
    try:
        r = run(lock_snippet('deploy_lock_acquire second'), env["env"])
        assert r.returncode == 1
        assert "REFUSED" in r.stderr and "what=holder" in r.stderr
    finally:
        os.killpg(holder.pid, 9)


def test_waiting_acquire_proceeds_once_released(env):
    holder = start_holder(env["env"], 1.5)
    t0 = time.monotonic()
    r = run(lock_snippet('deploy_lock_acquire_wait waiter 20 && echo GOT'), env["env"])
    assert r.returncode == 0 and "GOT" in r.stdout
    assert time.monotonic() - t0 >= 1.0
    holder.wait()


def test_waiting_acquire_times_out(env):
    holder = start_holder(env["env"], 8)
    try:
        t0 = time.monotonic()
        r = run(lock_snippet('deploy_lock_acquire_wait waiter 1'), env["env"])
        assert r.returncode == 1 and "TIMED OUT after 1s" in r.stderr
        assert time.monotonic() - t0 < 5
    finally:
        os.killpg(holder.pid, 9)


def test_nested_child_proceeds_instead_of_deadlocking(env):
    # Without nesting, the child's waiting acquire would block on its own parent until timeout.
    body = f'''
        deploy_lock_acquire_wait outer 5 || exit 9
        start=$SECONDS
        bash -c '. {LOCK_LIB}; deploy_lock_acquire_wait inner 30 && echo NESTED_OK' || exit 8
        echo "child_s=$((SECONDS-start))"
    '''
    r = run(lock_snippet(body), env["env"])
    assert r.returncode == 0, r.stderr
    assert "NESTED_OK" in r.stdout
    assert int(r.stdout.split("child_s=")[1].split()[0]) < 3
    assert "nested under" in r.stderr
    # Only the outermost holder records the outcome.
    assert "state=completed what=outer" in env["status"].read_text()
    assert lock_is_free(env["env"])


def test_foreign_token_does_not_nest(env):
    holder = start_holder(env["env"], 5)
    try:
        e = dict(env["env"], DEPLOY_LOCK_TOKEN="deadbeefdeadbeef")
        r = run(lock_snippet('deploy_lock_acquire intruder'), e)
        assert r.returncode == 1 and "REFUSED" in r.stderr
    finally:
        os.killpg(holder.pid, 9)


def test_stale_token_with_free_lock_takes_it_normally(env):
    e = dict(env["env"], DEPLOY_LOCK_TOKEN="deadbeefdeadbeef")
    Path(e["DEPLOY_LOCK_FILE"]).write_text("owner=x pid=1 what=old token=deadbeefdeadbeef\n")
    r = run(lock_snippet('deploy_lock_acquire fresh && cat "$DEPLOY_LOCK_FILE"'), e)
    assert r.returncode == 0 and "what=fresh" in r.stdout and "nested" not in r.stderr


def test_lock_released_when_holder_and_its_work_are_killed(env):
    holder = start_holder(env["env"], 30)
    os.killpg(holder.pid, 9); holder.wait()
    for _ in range(40):
        if lock_is_free(env["env"]):
            break
        time.sleep(0.05)
    assert lock_is_free(env["env"])


def test_lock_stays_held_while_work_started_under_it_still_runs(env):
    # Children inherit the descriptor: if a deploy script dies mid-build, the build it started
    # still holds the lock, so a backup cannot start beside it.
    holder = start_holder(env["env"], 30)
    try:
        holder.kill(); holder.wait()          # the shell only; its `sleep` keeps running
        assert not lock_is_free(env["env"])
    finally:
        os.killpg(holder.pid, 9)


# ── nightly backup ────────────────────────────────────────────────────────────────────────

def test_backup_publishes_verified_file_and_releases_lock(env):
    r = run(["bash", str(BACKUP)], env["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    final = env["backups"] / f"stockai-{TODAY}.sql.gz"
    assert final.exists() and not list(env["backups"].glob("*.partial"))
    assert MARKER in subprocess.run(["gzip", "-dc", str(final)], capture_output=True, text=True).stdout
    assert f"backup stockai-{TODAY}.sql.gz complete" in r.stdout
    assert calls(env) == ["dump held=1"]
    assert f"state=completed what=backup-{TODAY}" in env["status"].read_text()
    assert lock_is_free(env["env"])


def test_backup_waits_for_a_deploy_then_runs(env):
    holder = start_holder(env["env"], 1.5, what="deploy:frontend")
    r = run(["bash", str(BACKUP)], dict(env["env"], BACKUP_LOCK_WAIT_S="20"))
    holder.wait()
    assert r.returncode == 0, r.stdout + r.stderr
    assert (env["backups"] / f"stockai-{TODAY}.sql.gz").exists()


def test_backup_lock_timeout_fails_without_dumping(env):
    existing = env["backups"] / "stockai-2026-01-01.sql.gz"
    good_backup(existing)
    before = existing.read_bytes()
    holder = start_holder(env["env"], 8, what="deploy:frontend")
    try:
        r = run(["bash", str(BACKUP)], dict(env["env"], BACKUP_LOCK_WAIT_S="1"))
    finally:
        os.killpg(holder.pid, 9)
    assert r.returncode == 75
    assert "FAILED: lock_timeout after 1s" in r.stdout
    assert calls(env) == []
    assert existing.read_bytes() == before
    assert sorted(p.name for p in env["backups"].iterdir()) == ["stockai-2026-01-01.sql.gz"]


@pytest.mark.parametrize("mode,reason", [("fail", "dump_error: pg_dump exited 1"),
                                         ("nomarker", "verify_error: completion marker missing")])
def test_failed_dump_leaves_no_partial_and_keeps_same_day_good_backup(env, mode, reason):
    todays = env["backups"] / f"stockai-{TODAY}.sql.gz"
    good_backup(todays, payload="EARLIER GOOD RUN")
    before = todays.read_bytes()
    r = run(["bash", str(BACKUP)], dict(env["env"], FAKE_DUMP_MODE=mode))
    assert r.returncode == 1
    assert f"FAILED: {reason}" in r.stdout
    assert not list(env["backups"].glob("*.partial"))
    assert todays.read_bytes() == before
    assert f"state=failed what=backup-{TODAY}" in env["status"].read_text()
    assert lock_is_free(env["env"])


def test_insufficient_space_refuses_before_dumping(env):
    newest = env["backups"] / "stockai-2026-01-01.sql.gz"
    newest.write_bytes(b"x" * 1000)  # need = 2 x 1000 bytes
    r = run(["bash", str(BACKUP)], dict(env["env"], FAKE_DF_BYTES="1999"))
    assert r.returncode == 1
    assert "FAILED: insufficient_space: 1999 bytes free < 2000 required" in r.stdout
    assert calls(env) == [] and not list(env["backups"].glob("*.partial"))
    r = run(["bash", str(BACKUP)], dict(env["env"], FAKE_DF_BYTES="2000"))
    assert r.returncode == 0, r.stdout


def test_minimum_free_floor_applies_without_backups(env):
    r = run(["bash", str(BACKUP)], dict(env["env"], BACKUP_MIN_FREE_BYTES="5000000000",
                                        FAKE_DF_BYTES="4999999999"))
    assert r.returncode == 1 and "insufficient_space" in r.stdout


def test_failed_space_check_refuses_even_with_numeric_output(env):
    df = env["tmp"] / "bin" / "df"
    df.write_text("#!/usr/bin/env bash\nprintf 'Avail\\n100000000000\\n'\nexit 1\n")
    r = run(["bash", str(BACKUP)], env["env"])
    assert r.returncode == 1
    assert "FAILED: insufficient_space: could not read free space" in r.stdout
    assert calls(env) == []
    assert not list(env["backups"].iterdir())
    assert "state=failed" in env["status"].read_text()


def test_rotation_failure_reports_failure_and_keeps_published_backup(env):
    # Fail the first removal, then allow a later one: the loop must not hide the error.
    first = env["backups"] / "stockai-2026-01-01.sql.gz"
    second = env["backups"] / "stockai-2026-01-02.sql.gz"
    good_backup(first)
    good_backup(second)
    rm = env["tmp"] / "bin" / "rm"
    rm.write_text('#!/usr/bin/env bash\n'
                  'case "$*" in *stockai-2026-01-01.sql.gz*) exit 1 ;; esac\n'
                  'exec /bin/rm "$@"\n')
    rm.chmod(0o755)
    r = run(["bash", str(BACKUP)], dict(env["env"], BACKUP_KEEP="1"))
    assert r.returncode == 1
    assert "FAILED: rotation_error:" in r.stdout
    assert ".sql.gz complete" not in r.stdout
    assert first.exists() and second.exists()
    final = env["backups"] / f"stockai-{TODAY}.sql.gz"
    assert subprocess.run(["gzip", "-t", str(final)]).returncode == 0
    assert not list(env["backups"].glob("*.partial"))
    assert "state=failed" in env["status"].read_text()
    assert lock_is_free(env["env"])


def test_rotation_preserves_good_backups_after_failed_dump(env):
    days = [f"2026-01-0{i}" for i in range(1, 8)]
    for d in days:
        good_backup(env["backups"] / f"stockai-{d}.sql.gz")
    snapshot = {p.name: p.read_bytes() for p in env["backups"].iterdir()}

    r = run(["bash", str(BACKUP)], dict(env["env"], FAKE_DUMP_MODE="fail"))
    assert r.returncode == 1
    assert {p.name: p.read_bytes() for p in env["backups"].iterdir()} == snapshot

    # A leftover .partial from some earlier crash must neither survive nor count toward retention.
    (env["backups"] / f"stockai-{TODAY}.sql.gz.partial").write_bytes(b"truncated")
    r = run(["bash", str(BACKUP)], env["env"])
    assert r.returncode == 0, r.stdout
    names = sorted(p.name for p in env["backups"].iterdir())
    assert names == [f"stockai-{d}.sql.gz" for d in days[1:]] + [f"stockai-{TODAY}.sql.gz"]


def test_deploy_refuses_while_backup_runs(env):
    e = dict(env["env"], FAKE_DUMP_SLEEP="3")
    backup = subprocess.Popen(["bash", str(BACKUP)], env=e, stdout=subprocess.PIPE, text=True)
    lock = Path(e["DEPLOY_LOCK_FILE"])
    for _ in range(100):
        if lock.exists() and "what=backup-" in lock.read_text():
            break
        time.sleep(0.05)
    r = run(["bash", str(REMOTE), "signal-engine"], e)
    out, _ = backup.communicate(timeout=30)
    assert r.returncode == 75 and "REFUSED" in r.stderr and "what=backup-" in r.stderr
    assert backup.returncode == 0, out
    assert not any(c.startswith(("build", "up")) for c in calls(env))


# ── deploys ───────────────────────────────────────────────────────────────────────────────

def test_deploy_holds_lock_from_pull_through_build_recreate_and_prune(env):
    r = run(["bash", str(DEPLOY), "signal-engine"], env["env"], timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    # deploy_remote.sh reads the release identity with two read-only git calls (rev-parse, diff);
    # they must come AFTER the pull, so the SHA names the code being built, and under the lock.
    identity = [c for c in calls(env) if c.startswith(("git rev-parse", "git diff"))]
    remote_steps = [c for c in calls(env) if c.split()[0] in ("git", "build", "up", "prune")
                    and c not in identity]
    assert [c.split()[0] for c in remote_steps] == ["git", "build", "up", "prune", "prune"]
    assert all(c.endswith("held=1") for c in remote_steps + identity), remote_steps + identity
    log = calls(env)
    assert identity and all(log.index(remote_steps[0]) < log.index(c) < log.index(remote_steps[1])
                            for c in identity), log
    assert "nested under" in r.stderr  # deploy_remote.sh re-acquired as a nested holder
    assert "state=completed what=deploy.sh:signal-engine" in env["status"].read_text()
    assert lock_is_free(env["env"])


def test_outgoing_logs_saved_under_lock_after_build_before_recreate(env):
    r = run(["bash", str(REMOTE), "signal-engine"], env["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    order = [c.split()[0] for c in calls(env)]
    assert order.index("build") < order.index("logs") < order.index("up"), order
    assert "logs held=1" in calls(env)
    saved = list((env["tmp"] / "container-logs").glob("signal-engine-*.log.gz"))
    assert len(saved) == 1
    text = subprocess.run(["gunzip", "-c", str(saved[0])], capture_output=True, text=True).stdout
    assert "QueuePool limit reached" in text


def test_outgoing_logs_keep_newest_n(env):
    logs = env["tmp"] / "container-logs"
    logs.mkdir()
    for i in range(5):
        old = logs / f"signal-engine-2026010{i}T000000Z-before-x.log.gz"
        old.write_bytes(b"")
        os.utime(old, (1_000_000 + i, 1_000_000 + i))
    r = run(["bash", str(REMOTE), "signal-engine"], dict(env["env"], DEPLOY_LOG_KEEP="3"))
    assert r.returncode == 0, r.stdout + r.stderr
    kept = {p.name for p in logs.glob("signal-engine-*.log.gz")}
    old_kept = {n for n in kept if n.startswith("signal-engine-2026010")}
    assert len(kept) == 3, kept
    assert old_kept == {"signal-engine-20260103T000000Z-before-x.log.gz",
                        "signal-engine-20260104T000000Z-before-x.log.gz"}, kept


def test_failed_log_save_never_blocks_the_deploy(env):
    # gzip cannot write (read-only dir): the partial file is removed, the listing is then empty,
    # and an empty listing must not abort the deploy under set -e / pipefail.
    logs = env["tmp"] / "container-logs"
    logs.mkdir()
    logs.chmod(0o500)
    try:
        r = run(["bash", str(REMOTE), "signal-engine"], env["env"])
    finally:
        logs.chmod(0o700)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "could not save outgoing logs" in r.stdout + r.stderr
    assert any(c.startswith("up") for c in calls(env))


def test_unwritable_log_dir_never_blocks_the_deploy(env):
    blocker = env["tmp"] / "not-a-dir"
    blocker.write_text("x")
    r = run(["bash", str(REMOTE), "signal-engine"], dict(env["env"], DEPLOY_LOG_DIR=str(blocker / "sub")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "logs not saved" in r.stdout + r.stderr
    assert any(c.startswith("up") for c in calls(env))


def test_backup_started_during_deploy_waits_for_it(env):
    e = dict(env["env"], FAKE_STEP_SLEEP="1", BACKUP_LOCK_WAIT_S="30")
    deploy = subprocess.Popen(["bash", str(DEPLOY), "signal-engine"], env=e,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lock = Path(e["DEPLOY_LOCK_FILE"])
    for _ in range(200):
        if lock.exists() and "what=deploy.sh" in lock.read_text():
            break
        time.sleep(0.05)
    r = run(["bash", str(BACKUP)], e)
    deploy.communicate(timeout=60)
    assert r.returncode == 0 and deploy.returncode == 0
    order = [c.split()[0] for c in calls(env)]
    assert order.index("dump") > len(order) - 1 - order[::-1].index("prune")


def test_failed_build_stops_before_recreate_and_releases_lock(env):
    r = run(["bash", str(DEPLOY), "signal-engine"], dict(env["env"], FAKE_BUILD_FAIL="1"))
    assert r.returncode != 0
    assert not any(c.startswith("up") for c in calls(env))
    assert "state=failed" in env["status"].read_text()
    assert lock_is_free(env["env"])


def test_deploy_refused_by_held_lock_exits_75_without_building(env):
    holder = start_holder(env["env"], 8, what="backup-2026-10-22")
    try:
        r = run(["bash", str(DEPLOY), "signal-engine"], env["env"])
    finally:
        os.killpg(holder.pid, 9)
    assert r.returncode == 1 and "REFUSED" in r.stderr
    assert not any(c.split()[0] in ("git", "build", "up") for c in calls(env))


def test_deploy_rejects_arguments_that_are_not_service_names(env):
    r = run(["bash", str(DEPLOY), "signal-engine;rm"], env["env"])
    assert r.returncode == 1 and "invalid argument" in r.stderr and calls(env) == []


def test_dockerignore_excludes_git():
    lines = (REPO / ".dockerignore").read_text().splitlines()
    assert ".git" in lines


@pytest.mark.parametrize("entry", [REMOTE, DEPLOY])
@pytest.mark.parametrize("state", ["running/unhealthy", "running/starting", "running/none", "exited/healthy", "missing"])
def test_failed_health_fails_deployment_and_status_even_after_pruning(env, entry, state):
    r = run(["bash", str(entry), "signal-engine"], dict(env["env"], FAKE_HEALTH_STATE=state))
    assert r.returncode != 0, r.stdout + r.stderr
    assert "deployment verification failed" in r.stdout
    assert "state=failed" in env["status"].read_text()
    assert "exit=1" in env["status"].read_text()
    assert any(c.startswith("prune") for c in calls(env))
    assert lock_is_free(env["env"])


@pytest.mark.parametrize("failure", ["FAKE_INSPECT_FAIL", "FAKE_CONFIG_FAIL", "FAKE_RUNNING"])
def test_incomplete_container_verification_cannot_succeed(env, failure):
    value = "false" if failure == "FAKE_RUNNING" else "1"
    r = run(["bash", str(DEPLOY), "signal-engine"], dict(env["env"], **{failure: value}))
    assert r.returncode != 0, r.stdout + r.stderr
    assert "state=failed" in env["status"].read_text()
    if failure == "FAKE_CONFIG_FAIL":
        assert "all declared services present" not in r.stdout
