import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import home_git

spec = importlib.util.spec_from_file_location("home_git_remote", TOOLS / "home_git_remote.py")
home_git_remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home_git_remote)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def make_repo(path):
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    (path / "tracked.txt").write_text("base\n")
    git(path, "add", "tracked.txt")
    git(path, "commit", "-qm", "initial")
    return path


def test_local_bare_remote_exact_refs_owner_classes_and_unavailable(tmp_path):
    root = make_repo(tmp_path / "repo")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(root, "remote", "add", "origin", str(remote))
    git(root, "remote", "add", "external", str(remote))
    git(root, "remote", "add", "offline", str(tmp_path / "missing.git"))
    git(root, "push", "-q", "origin", "main")
    git(root, "tag", "v1", "main")
    git(root, "switch", "-qc", "topic")
    (root / "tracked.txt").write_text("stashed\n")
    git(root, "stash", "push", "-qm", "subject omitted")
    (root / "tracked.txt").write_text("topic commit\n")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-qm", "unpublished")

    inventory = tmp_path / "local.sqlite"
    home_git.inspect([root], inventory, local_only=True)
    with sqlite3.connect(inventory) as conn:
        conn.execute("UPDATE remotes SET github_identity='SisoDias/demo' WHERE name='origin'")
        conn.execute("UPDATE remotes SET github_identity='outside/demo' WHERE name='external'")
        conn.commit()

    active = 0
    max_active = 0
    guard = threading.Lock()

    def runner(args, **kwargs):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.03)
            return subprocess.run(args, **kwargs)
        finally:
            with guard:
                active -= 1

    jobs, skipped = home_git_remote.load_inventory([inventory], [])
    output = tmp_path / "remote.sqlite"
    probes, refs, count = home_git_remote.run_probes(jobs, skipped, output, runner=runner)

    assert count == 3 and max_active <= 2
    assert probes == {"available": 2, "unavailable": 1}
    with sqlite3.connect(output) as conn:
        probe_rows = conn.execute("SELECT remote_name,owner_class,status FROM remote_probes").fetchall()
        evidence = conn.execute("SELECT remote_name,refname,status FROM ref_evidence").fetchall()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(remote_probes)")}
        stored = Path(output).read_bytes()
    assert {row[0]: (row[1], row[2]) for row in probe_rows} == {
        "origin": ("own_sisodias", "available"),
        "external": ("external_github", "available"),
        "offline": ("unknown_non_github", "unavailable"),
    }
    origin = {(name, status) for remote_name, name, status in evidence if remote_name == "origin"}
    assert ("refs/heads/main", "match") in origin
    assert ("refs/heads/topic", "absent_at_named_ref") in origin
    assert ("refs/tags/v1", "absent_at_named_ref") in origin
    assert ("refs/stash", "absent_at_named_ref") in origin
    assert ("stash@{0}", "not_advertisable_ref") in origin
    assert ("HEAD", "absent_at_named_ref") in origin
    assert "url" not in columns and b"subject omitted" not in stored
    assert output.stat().st_mode & 0o777 == 0o600


def test_protected_prefix_skips_network_and_keeps_ref_evidence(tmp_path):
    root = make_repo(tmp_path / "protected")
    bare = tmp_path / "protected-remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(root, "remote", "add", "origin", str(bare))
    inventory = tmp_path / "local.sqlite"
    home_git.inspect([root], inventory, local_only=True)
    jobs, skipped = home_git_remote.load_inventory([inventory], [str(root)])
    assert jobs == [] and len(skipped) == 1

    def fail_if_called(*args, **kwargs):
        raise AssertionError("protected roots must not initiate remote probes")

    output = tmp_path / "remote.sqlite"
    probes, refs, _ = home_git_remote.run_probes(jobs, skipped, output, runner=fail_if_called)
    assert probes == {"protected_skipped": 1}
    assert refs.get("protected_skipped", 0) >= 1


def test_prefix_nested_inside_repository_protects_common_dir(tmp_path):
    root = make_repo(tmp_path / "repo")
    git(root, "remote", "add", "origin", str(tmp_path / "unavailable.git"))
    inventory = tmp_path / "local.sqlite"
    home_git.inspect([root], inventory, local_only=True)
    nested_prefix = root / "private-payload"
    jobs, skipped = home_git_remote.load_inventory([inventory], [str(nested_prefix)])
    assert jobs == [] and len(skipped) == 1
