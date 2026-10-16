import importlib.util
import os
from pathlib import Path
import subprocess


TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("home_git", TOOLS / "home_git.py")
home_git = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home_git)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def repo(path):
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    (path / "tracked.txt").write_text("base\n")
    git(path, "add", "tracked.txt")
    git(path, "commit", "-qm", "initial")
    return path


def test_inventory_captures_branches_tags_stashes_dirty_and_is_read_only(tmp_path):
    root = repo(tmp_path / "repo")
    git(root, "tag", "v1")
    git(root, "switch", "-qc", "topic")
    (root / "tracked.txt").write_text("changed\n")
    git(root, "stash", "push", "-qm", "private stash subject")
    (root / "untracked secret name.txt").write_text("not read")

    index_before = (root / ".git" / "index").read_bytes()
    record, remotes, refs, checks = home_git.inspect_root(root)
    index_after = (root / ".git" / "index").read_bytes()

    assert record["current_branch"] == "topic"
    assert record["detached"] == 0
    assert record["dirty"] == 1 and record["changed_entries"] == 1
    assert record["snapshot_stable"] == 1 and record["error"] is None
    assert {ref["refname"] for ref in refs} >= {
        "refs/heads/main", "refs/heads/topic", "refs/tags/v1", "stash@{0}", "HEAD"
    }
    assert all("private stash subject" not in ref["refname"] for ref in refs)
    assert remotes == {} and checks == []
    assert index_before == index_after


def test_worktree_records_gitdir_and_common_dir(tmp_path):
    root = repo(tmp_path / "main")
    worktree = tmp_path / "linked"
    git(root, "worktree", "add", "-qb", "linked", str(worktree))

    record, _, _, _ = home_git.inspect_root(worktree)

    assert record["error"] is None
    assert Path(record["git_dir"]) != Path(record["common_dir"])
    assert Path(record["common_dir"]) == root / ".git"
    assert record["current_branch"] == "linked"


def test_detached_head_and_bare_repository_are_supported(tmp_path):
    root = repo(tmp_path / "source")
    git(root, "checkout", "--detach", "-q")
    record, _, refs, _ = home_git.inspect_root(root)
    assert record["detached"] == 1
    assert record["current_branch"] is None
    assert any(ref["refname"] == "HEAD" for ref in refs)

    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(root), str(bare)], check=True)
    bare_record, _, _, _ = home_git.inspect_root(bare)
    assert bare_record["error"] is None
    assert bare_record["bare"] == 1
    assert Path(bare_record["git_dir"]) == bare
    assert Path(bare_record["common_dir"]) == bare


def test_protected_halo_remote_is_sanitized_and_never_probed(tmp_path):
    root = repo(tmp_path / "repo")
    git(root, "remote", "add", "halo", "https://user:secret@github.com/CamronKellman/HALO-AGENCY.git?token=secret")
    git(root, "remote", "add", "crm", "git@github.com:CamronKellman/halocrm.git")
    probed = []

    def visibility(identity, runner):
        probed.append(identity)
        return "private", None

    record, remotes, _, checks = home_git.inspect_root(root, visibility_probe=visibility)

    assert record["protected"] == 1
    assert probed == [] and checks == []
    assert remotes["halo"]["protected"] == 1
    assert remotes["crm"]["protected"] == 1
    assert remotes["halo"]["github_identity"] == "CamronKellman/HALO-AGENCY"
    assert "secret" not in remotes["halo"]["fetch_url"]
    assert remotes["halo"]["fetch_url"] == "https://github.com/CamronKellman/HALO-AGENCY.git"


def test_local_only_skips_visibility_and_ref_network_probes(tmp_path):
    root = repo(tmp_path / "repo")
    git(root, "remote", "add", "origin", "https://github.com/example/project.git")
    called = []

    def forbidden_probe(*args):
        called.append("visibility")
        raise AssertionError("local-only mode must not make network-adjacent probes")

    def forbidden_runner(args, **kwargs):
        if args[:3] == ["git", "--no-optional-locks", "ls-remote"]:
            called.append("ls-remote")
            raise AssertionError("local-only mode must not execute ls-remote")
        return home_git.run(args, **kwargs)

    record, remotes, _, checks = home_git.inspect_root(
        root, runner=forbidden_runner, visibility_probe=forbidden_probe, local_only=True
    )
    assert record["error"] is None
    assert remotes["origin"]["visibility"] is None
    assert checks == []
    assert called == []


def test_scope_prefix_protects_root_and_common_gitdir(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    root = repo(parent / "nested")
    git(root, "remote", "add", "origin", "https://github.com/example/project.git")
    scope = parent
    probed = []
    def visibility(identity, runner):
        probed.append(identity)
        return "private", None
    record, _, _, checks = home_git.inspect_root(root, visibility_probe=visibility, scope_prefixes=[str(scope)])
    assert record["protected"] == 1
    assert checks == [] and probed == []

    other = repo(tmp_path / "other")
    common_scope = other / ".git"
    record, _, _, _ = home_git.inspect_root(other, scope_prefixes=[str(common_scope)])
    assert record["protected"] == 1


def test_bad_root_does_not_prevent_other_roots_and_output_is_private(tmp_path):
    good = repo(tmp_path / "good")
    output = tmp_path / "private" / "inventory.sqlite"
    counts = home_git.inspect([good, tmp_path / "missing"], output)

    assert counts[:2] == (2, 1)
    assert output.stat().st_mode & 0o777 == 0o600
    import sqlite3
    with sqlite3.connect(output) as conn:
        rows = conn.execute("SELECT root,error FROM repositories ORDER BY root").fetchall()
    assert len(rows) == 2
    assert next(error for root, error in rows if root.endswith("missing"))
