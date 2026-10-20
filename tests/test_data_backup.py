#!/usr/bin/env python3
"""data-backup.py: a plane's machine, root and prune; and the guard that keeps an emptied plane's last snapshot.

  python3 -m unittest tests/test_data_backup.py -v
"""
import contextlib, importlib.util, io, json, os, shutil, subprocess, sys, tarfile, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
spec = importlib.util.spec_from_file_location("data_backup", os.path.join(TOOLS, "data-backup.py"))
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


class DataBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv = os.path.join(self.tmp, "srv")
        prof = os.path.join(self.srv, "opt", "app", "data", "sessions", "Default")
        os.makedirs(os.path.join(prof, "Code Cache"))
        open(os.path.join(prof, "Local State"), "w").write("keep")
        open(os.path.join(prof, "Code Cache", "x.js"), "w").write("cache")
        open(os.path.join(self.srv, "opt", "app", "data", "api-key"), "w").write("secret")
        self.plane = {"backend": "github:siso-data/siso-data-t", "name": "t", "machine": "vps", "root": self.srv, "encrypt": False,
                      "paths": ["opt/app/data"], "exclude": ["opt/app/data/api-key"], "prune": ["Code Cache"]}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_root_prune_exclude(self):
        inc, _ = db.gather(self.plane)
        rel = sorted(os.path.relpath(p, self.srv) for p in inc)
        self.assertEqual(rel, ["opt/app/data/sessions/Default/Local State"])

    def test_machine_filter(self):
        planes = [{"name": "a", "paths": []}, {"name": "b", "machine": "vps", "paths": []}]
        orig = db.json.load
        db.json.load = lambda f: {"planes": planes}
        try:
            self.assertEqual([p["name"] for p in db.planes("laptop")], ["a"])
            self.assertEqual([p["name"] for p in db.planes("vps")], ["b"])
            self.assertEqual(len(db.planes()), 2)
        finally:
            db.json.load = orig

    def test_git_hook_environment_cannot_redirect_plane_repository(self):
        sentinel = os.path.join(self.tmp, "sentinel")
        isolated = os.path.join(self.tmp, "isolated")
        os.makedirs(sentinel)
        os.makedirs(isolated)
        def git(cwd, *args):
            return subprocess.check_output(["git", "-C", cwd, *args], text=True).strip()
        git(sentinel, "init", "-q", "-b", "main")
        git(sentinel, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--allow-empty", "-qm", "sentinel")
        before = git(sentinel, "rev-parse", "HEAD")
        poisoned = {"GIT_DIR": os.path.join(sentinel, ".git"), "GIT_WORK_TREE": sentinel,
                    "GIT_COMMON_DIR": os.path.join(sentinel, ".git"),
                    "GIT_INDEX_FILE": os.path.join(sentinel, ".git", "index"),
                    "GIT_ALLOW_PROTOCOL": "file"}
        with patch.dict(os.environ, poisoned):
            self.assertEqual(db.sh("git", "init", "-q", "-b", "main", cwd=isolated)[0], 0)
            with open(os.path.join(isolated, "fixture"), "w") as out:
                out.write("isolated fixture\n")
            self.assertEqual(db.sh("git", "add", "fixture", cwd=isolated)[0], 0)
            self.assertEqual(db.sh("git", "-c", "user.name=Fixture", "-c",
                                  "user.email=fixture@example.invalid", "commit", "-qm",
                                  "plane fixture", cwd=isolated)[0], 0)
        self.assertEqual(git(sentinel, "rev-parse", "HEAD"), before)
        self.assertEqual(git(sentinel, "status", "--porcelain"), "")
        self.assertEqual(git(isolated, "ls-files"), "fixture")


    def test_emptied_plane_is_held(self):
        shutil.rmtree(os.path.join(self.srv, "opt"))
        os.makedirs(os.path.join(self.srv, "opt", "app", "data"))
        m = os.path.join(self.tmp, "machines", "vps")
        os.makedirs(m)
        json.dump({"results": {"t": {"status": "pushed", "files": 275, "fingerprint": "old"}}},
                  open(os.path.join(m, "data-backup.json"), "w"))
        orig = db.REPO
        db.REPO = self.tmp
        try:
            r = db.run_plane(self.plane, "vps")
        finally:
            db.REPO = orig
        self.assertEqual(r["status"], "held")
        self.assertEqual(r["files"], 275)

    def test_plain_plane_with_a_secret_is_held(self):
        leak = os.path.join(self.srv, "opt", "app", "data", "sessions", "Default", "config.env")
        open(leak, "w").write("GITHUB_TOKEN=ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + "\n")
        inc, _ = db.gather(self.plane)
        found = db.secrets_in(self.plane, inc)
        self.assertEqual([f["file"] for f in found], ["opt/app/data/sessions/Default/config.env"])
        os.unlink(leak)
        self.assertEqual(db.secrets_in(self.plane, db.gather(self.plane)[0]), [])

    def test_plain_keep_large_secret_after_twenty_mb_is_scanned(self):
        large = Path(self.srv) / "opt/app/data/large.jsonl"
        with large.open("wb") as output:
            padding = b"ordinary synthetic fixture line\n" * 32768
            while output.tell() <= 21 * 1024 * 1024:
                output.write(padding)
            output.write(("GITHUB_TOKEN=ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + "\n").encode())
        self.plane["keep_large"] = ["opt/app/data/large.jsonl"]
        inc, omitted = db.gather(self.plane)
        self.assertIn(str(large), inc)
        self.assertEqual(omitted, [])
        self.assertGreater(large.stat().st_size, 20 * 1024 * 1024)
        findings = db.secrets_in(self.plane, inc)
        self.assertEqual([f["file"] for f in findings], ["opt/app/data/large.jsonl"])

    def test_scanner_failure_or_missing_invalid_report_fails_closed(self):
        def scanner(code, report):
            def run(args, **kwargs):
                self.assertEqual(args[args.index("--max-target-megabytes") + 1], "0")
                self.assertEqual(kwargs["timeout"], 3600)
                if report is not None:
                    Path(args[args.index("-r") + 1]).write_text(report)
                return subprocess.CompletedProcess(args, code, "", "synthetic private diagnostic")
            return run
        for code, report in [(0, None), (1, None), (2, None), (1, "[]"), (0, "invalid"), (0, "{}")]:
            with self.subTest(code=code, report=report), patch.object(db.subprocess, "run", side_effect=scanner(code, report)):
                findings = db.secrets_in(self.plane, db.gather(self.plane)[0])
                self.assertTrue(findings)
                self.assertNotIn("private diagnostic", json.dumps(findings))

    def run_local_snapshot(self, fail_add=False):
        ws = os.path.join(self.tmp, "workspace")
        os.makedirs(os.path.join(ws, "input"))
        payload = os.urandom(32768)
        source = os.path.join(ws, "input", "payload.bin")
        with open(source, "wb") as f:
            f.write(payload)
        remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        real_sh = db.sh
        released = []

        def local_sh(*args, **kwargs):
            if args[0] == "gh":
                return 0, "true" if args[-1] == ".private" else "", ""
            if args[:4] == ("git", "remote", "add", "origin"):
                args = (*args[:4], remote)
            if args[:2] == ("git", "commit"):
                cwd = kwargs["cwd"]
                _, files, _ = real_sh("git", "ls-files", cwd=cwd)
                for name in files.splitlines():
                    if name.startswith("data.tar.zst."):
                        self.assertFalse(os.path.exists(os.path.join(cwd, name)))
                        self.assertEqual(real_sh("git", "cat-file", "-t", ":" + name, cwd=cwd)[:2], (0, "blob"))
                        released.append(name)
            if fail_add and args[:2] == ("git", "add") and args[2].startswith("data.tar.zst."):
                return 1, "", "simulated full disk"
            return real_sh(*args, **kwargs)

        plane = {"name": "chunk-test", "root": ws, "paths": ["input"], "encrypt": False, "backend": "github:siso-data/siso-data-chunk-test"}
        with patch.object(db, "WS", ws), patch.object(db, "REPO", self.tmp), \
                patch.object(db, "WORK", os.path.join(ws, "backup-repos")), \
                patch.object(db, "CHUNK", 4096), patch.object(db, "PUSH_BATCH", 8192), \
                patch.object(db, "admit_upload", return_value=(True, "fixture")), \
                patch.object(db, "release_reservation"), \
                patch.object(db, "secrets_in", return_value=[]), patch.object(db, "sh", side_effect=local_sh):
            result = db.run_plane(plane, "laptop")
        with open(source, "rb") as f:
            self.assertEqual(f.read(), payload)
        return result, remote, payload, released, ws

    def test_released_chunk_workfiles_restore_from_remote(self):
        result, remote, payload, released, ws = self.run_local_snapshot()
        self.assertEqual(result["status"], "pushed")
        self.assertGreater(len(set(released)), 1)
        names = subprocess.check_output(["git", "-C", remote, "ls-tree", "--name-only", result["ref"]], text=True).splitlines()
        packed = b"".join(subprocess.check_output(["git", "-C", remote, "show", result["ref"] + ":" + name])
                          for name in sorted(names) if name.startswith("data.tar.zst."))
        archive = subprocess.run(["zstd", "-q", "-d", "-c"], input=packed, capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            self.assertEqual(tar.extractfile("input/payload.bin").read(), payload)
        self.assertFalse(os.path.exists(os.path.join(ws, "backup-repos", "siso-data-chunk-test")))

    def test_failed_independent_download_never_publishes_success_tag(self):
        with patch.object(db.dm, "verified_archive", side_effect=RuntimeError("corrupt remote")):
            result, remote, _, _, _ = self.run_local_snapshot()
        self.assertEqual(result["status"], "error")
        self.assertEqual(subprocess.check_output(["git", "-C", remote, "tag", "-l"], text=True), "")

    def test_failed_remote_extraction_never_publishes_success_tag(self):
        with patch.object(db.dm, "extract_archive", side_effect=ValueError("unsafe remote archive")):
            result, remote, _, _, _ = self.run_local_snapshot()
        self.assertEqual(result["status"], "error")
        self.assertEqual(subprocess.check_output(["git", "-C", remote, "tag", "-l"], text=True), "")

    def test_failed_admission_does_not_allocate_stage_or_call_git(self):
        with patch.object(db, "WS", self.tmp), patch.object(db, "REPO", self.tmp), \
                patch.object(db, "secrets_in", return_value=[]), \
                patch.object(db, "admit_upload", return_value=(False, "capacity held")), patch.object(db, "sh") as shell:
            result = db.run_plane(self.plane, "vps")
        self.assertEqual(result["status"], "held")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "_data/estate-stage")))
        shell.assert_not_called()

    def test_add_failure_keeps_chunk_workfile_and_source(self):
        result, remote, payload, released, ws = self.run_local_snapshot(fail_add=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("add chunk", result["error"])
        repo = next(str(p) for p in __import__("pathlib").Path(ws, "backup-repos").glob("siso-data-chunk-test-*"))
        self.assertTrue(any(name.startswith("data.tar.zst.") for name in os.listdir(repo)))
        self.assertFalse(subprocess.run(["git", "-C", remote, "show-ref"], capture_output=True).stdout)


class ReportingPrivacy(unittest.TestCase):
    """Only synthetic paths reach the archive input; none reach plaintext reporting."""

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.tmp) / "SYNTHETIC_ROOT_SENTINEL"
        self.source = self.root / "SYNTHETIC_TOP_SENTINEL/SYNTHETIC_FOLDER_SENTINEL/SYNTHETIC_FILE_SENTINEL.txt"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("synthetic fixture only\n")
        self.plane = {"name": "fixture", "root": str(self.root), "paths": ["SYNTHETIC_TOP_SENTINEL"], "backend": "github:siso-data/siso-data-fixture"}
        self.remote = Path(self.tmp) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.real_sh, self.real_run = db.sh, subprocess.run
        self.calls, self.archive_inputs = [], []
        self.failure = None
        self.diagnostic = str(self.source) + ": synthetic failure"
        for name, value in [("WS", str(self.root)), ("REPO", str(self.root)),
                            ("WORK", str(self.root / "repos"))]:
            self.enterContext(patch.object(db, name, value))
        self.enterContext(patch.object(db, "admit_upload", return_value=(True, "fixture")))
        self.enterContext(patch.object(db, "release_reservation"))
        self.enterContext(patch.object(db.dm, "extract_archive"))  # opaque fake encrypted archive; transport still independently downloaded
        self.enterContext(patch.object(db, "recipient", return_value="synthetic-recipient"))
        self.enterContext(patch.object(db, "sh", side_effect=self.routed))
        self.enterContext(patch.object(db.subprocess, "run", side_effect=self.pipeline))

    def pipeline(self, args, **kwargs):
        if args[:3] != ["bash", "-o", "pipefail"]:
            return self.real_run(args, **kwargs)
        # Do not encrypt/decrypt or open anything except this fixture's archive inputs.
        stage = next((self.root / "_data/estate-stage").iterdir())
        manifest = json.loads((stage / "MANIFEST.json").read_text())
        self.archive_inputs.append((manifest, (stage / "files.lst").read_bytes()))
        (stage / "tar.err").write_text(self.diagnostic)
        if self.failure in ("tar", "pipeline"):
            return subprocess.CompletedProcess(args, 2, "", self.diagnostic if self.failure == "pipeline" else "")
        (stage / "chunks/data.tar.zst.age.aaa").write_bytes(b"synthetic opaque chunk")
        return subprocess.CompletedProcess(args, 0, "", "")

    def routed(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("git", "rev-parse") and self.failure == "resolve":
            return 1, "", self.diagnostic
        if args[:2] == ("git", "ls-remote") and self.failure == "timeout":
            raise subprocess.TimeoutExpired(["git", self.diagnostic], 1, stderr=self.diagnostic)
        if args[:2] == ("git", "add"):
            failure = "add chunk" if args[2].startswith("data.tar.zst.") else "add completion" if args[2] == "SNAPSHOT.json" else "add summary"
            if self.failure == failure:
                return 1, "", self.diagnostic
        if args[:2] == ("git", "commit") and self.failure == "commit summary":
            return 1, "", self.diagnostic
        if args[0] == "gh":
            if args[1:3] == ("repo", "view"):
                return 1, "", ""
            if args[1:3] == ("repo", "create") and self.failure == "create":
                return 1, "", self.diagnostic
            return 0, "true" if args[-1] == ".private" else "", ""
        if args[:4] == ("git", "remote", "add", "origin"):
            args = (*args[:4], str(self.remote))
        if args[:2] in (("git", "ls-remote"), ("git", "clone")):
            args = tuple(str(self.remote) if str(a).startswith("https://github.com/") else a for a in args)
        self.assertIn(args[0], ("git", "tar"))
        return self.real_sh(*args, **kwargs)

    def assert_private(self, text):
        for marker in ("SYNTHETIC_ROOT_SENTINEL", "SYNTHETIC_TOP_SENTINEL", "SYNTHETIC_FOLDER_SENTINEL", "SYNTHETIC_FILE_SENTINEL"):
            self.assertNotIn(marker, text)

    def test_plaintext_snapshot_and_description_preserve_only_aggregate_metadata(self):
        result = db.run_plane(self.plane, "laptop")
        self.assertEqual(result["status"], "pushed", result)
        _, names, _ = self.real_sh("git", "ls-tree", "--name-only", result["ref"], cwd=str(self.remote))
        self.assertEqual(set(names.splitlines()), {"SUMMARY.md", "RESTORE.md", "SNAPSHOT.json", "DATA-MANIFEST.json", "data.tar.zst.age.aaa"})
        for name in names.splitlines():
            _, content, _ = self.real_sh("git", "show", result["ref"] + ":" + name, cwd=str(self.remote))
            self.assert_private(content)
        _, summary, _ = self.real_sh("git", "show", result["ref"] + ":SUMMARY.md", cwd=str(self.remote))
        self.assertIn("1 files", summary)
        self.assertIn(result["ref"].rsplit("/", 1)[1], summary)
        descriptions = [a[a.index("--description") + 1] for a in self.calls if a[:3] == ("gh", "repo", "create")]
        self.assertEqual(len(descriptions), 0)
        self.assert_private(json.dumps(descriptions) + json.dumps(result))
        _, messages, _ = self.real_sh("git", "log", "--format=%B", result["ref"], cwd=str(self.remote))
        self.assert_private(messages)
        manifest, file_list = self.archive_inputs[0]
        rel = str(self.source.relative_to(self.root))
        self.assertEqual(manifest["files"], [{"path": rel, "bytes": self.source.stat().st_size}])
        self.assertIn(rel.encode(), file_list.split(b"\0"))
        self.assertEqual(manifest["plane"], "fixture")
        self.assertEqual(manifest["regenerable_not_included"], [])

    def test_completed_generations_survive_partial_upload_and_restore_uses_pinned_tag(self):
        first = db.run_plane(self.plane, "laptop")
        self.assertEqual(first["status"], "pushed", first)
        self.source.write_text("second synthetic payload\n")
        second = db.run_plane(self.plane, "laptop")
        self.assertEqual(second["status"], "pushed", second)
        self.assertNotEqual(first["ref"], second["ref"])
        for result in (first, second):
            self.assertEqual(self.real_sh("git", "rev-parse", result["ref"], cwd=str(self.remote))[1], result["commit"])
        self.failure = "add completion"
        failed = db.run_plane(self.plane, "laptop")
        self.assertEqual(failed["status"], "error", failed)
        _, tags, _ = self.real_sh("git", "for-each-ref", "--format=%(refname)", "refs/tags/snapshots", cwd=str(self.remote))
        self.assertEqual(set(tags.splitlines()), {first["ref"], second["ref"]})
        self.failure = None
        with patch.object(db, "planes", return_value=[self.plane]):
            self.assertEqual(db.cmd_restore("fixture", str(self.root / "restore")), 0)
        clones = [a for a in self.calls if a[:2] == ("git", "clone")]
        self.assertEqual(clones[-1][clones[-1].index("--branch") + 1], second["ref"].removeprefix("refs/tags/"))

    def test_large_unclassified_file_holds_before_unchanged_shortcut_or_upload(self):
        import hashlib
        included = [str(self.source)]
        fingerprint = hashlib.sha256("\n".join(
            f"{p}\t{os.path.getsize(p)}\t{int(os.path.getmtime(p))}" for p in included).encode()).hexdigest()
        records = self.root / "machines/laptop/data-backup.json"
        records.parent.mkdir(parents=True)
        records.write_text(json.dumps({"results": {"fixture": {"status": "pushed", "fingerprint": fingerprint, "files": 1}}}))
        large = self.source.parent / "SYNTHETIC_DATABASE.db"
        with large.open("wb") as stream:
            stream.truncate(db.REGEN_MIN)
        result = db.run_plane(self.plane, "laptop")
        self.assertEqual((result["status"], result["unclassified_large_files"]), ("held", 1))
        self.assert_private(json.dumps(result))
        self.assertEqual(self.calls, [])

    def test_public_or_unverified_destination_never_receives_payload(self):
        routed = self.routed
        def public(*args, **kwargs):
            if args[0] == "gh" and args[-1] == ".private":
                return 0, "false", ""
            return routed(*args, **kwargs)
        with patch.object(db, "sh", side_effect=public):
            result = db.run_plane(self.plane, "laptop")
        self.assertEqual(result["status"], "held", result)
        self.assertFalse(any("push" in call for call in self.calls))

    def test_backup_errors_never_echo_path_diagnostics(self):
        for failure in ("tar", "pipeline", "add summary", "commit summary", "add chunk", "add completion", "resolve", "timeout"):
            with self.subTest(failure=failure):
                self.failure = failure
                result = db.run_plane(self.plane, "laptop")
                self.assertEqual(result["status"], "error", result)
                self.assert_private(json.dumps(result))
        _, refs, _ = self.real_sh("git", "for-each-ref", "refs/tags/snapshots", cwd=str(self.remote))
        self.assertEqual(refs, "")

    def test_source_stat_failure_is_a_private_error_receipt(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(db, "gather", side_effect=OSError(5, "synthetic stat failure", str(self.source))), \
                patch.object(db, "planes", return_value=[self.plane]), \
                patch.object(sys, "argv", ["data-backup.py", "run", "fixture"]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            db.main()
        receipt = json.loads((self.root / "machines/laptop/data-backup.json").read_text())
        self.assertEqual(receipt["results"]["fixture"]["status"], "error")
        self.assert_private(output.getvalue() + errors.getvalue() + json.dumps(receipt))
        self.assertEqual(self.calls, [])

    def test_skipped_git_source_plan_reports_counts_without_paths(self):
        (self.source.parent.parent / ".git").mkdir()
        (self.root / "machines/laptop").mkdir(parents=True)
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(db, "planes", return_value=[self.plane]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            db.cmd_plan("laptop")
        plan = json.loads((self.root / "machines/laptop/data-plan.json").read_text())
        self.assertEqual(plan["planes"][0]["files"], 0)
        self.assertIn("1", errors.getvalue())
        self.assert_private(output.getvalue() + errors.getvalue() + json.dumps(plan))

    def test_restore_transport_errors_do_not_echo_paths(self):
        ref = "refs/tags/snapshots/laptop/synthetic"
        for replies in ([(1, "", self.diagnostic)],
                        [(0, "a" * 40 + "\t" + ref, ""), (1, "", self.diagnostic)],
                        [subprocess.TimeoutExpired(["git", self.diagnostic], 1)]):
            with self.subTest(replies=replies), patch.object(db, "planes", return_value=[self.plane]), \
                    patch.object(db, "sh", side_effect=replies), self.assertRaises(SystemExit) as raised:
                db.cmd_restore("fixture", str(self.root / "restore"), ref=ref)
            self.assertNotEqual(raised.exception.code, 0)
            self.assert_private(str(raised.exception.code))

    def test_legacy_restore_decoder_failure_is_private_and_unsuccessful(self):
        ref = "refs/tags/snapshots/laptop/synthetic"
        checkout = self.root / "restore-checkout"
        checkout.mkdir()
        (checkout / "data.tar.zst.age.aaa").write_bytes(b"synthetic")
        (checkout / "SNAPSHOT.json").write_text(json.dumps({"plane": "fixture", "machine": "laptop",
                                                          "complete": True, "chunks": 1, "encrypted_bytes": 9}))
        output, errors = io.StringIO(), io.StringIO()
        replies = [(0, "a" * 40 + "\t" + ref, ""), (0, "", ""), (0, "a" * 40, "")]
        with patch.object(db, "planes", return_value=[self.plane]), patch.object(db, "sh", side_effect=replies), \
                patch.object(db.tempfile, "mkdtemp", return_value=str(checkout)), \
                patch.object(db.dm, "extract_archive", side_effect=RuntimeError(self.diagnostic)) as extract, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            db.cmd_restore("fixture", str(self.root / "restore"), ref=ref)
        self.assertNotEqual(raised.exception.code, 0)
        extract.assert_called_once()
        self.assert_private(str(raised.exception.code))
        self.assert_private(output.getvalue() + errors.getvalue())

    def test_restore_extraction_errors_do_not_echo_paths(self):
        with patch.object(db, "_cmd_restore", side_effect=ValueError(self.diagnostic)), self.assertRaises(SystemExit) as raised:
            db.cmd_restore("fixture", str(self.root / "restore"))
        self.assert_private(str(raised.exception.code))


class PreservedRestore(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(db, "REPO", str(self.root)))
        self.enterContext(patch.object(db, "planes", return_value=[{"name": "fixture", "backend": "github:siso-data/unprovisioned", "owner": "legacy-org", "repo": "legacy-repo"}]))
        self.shell = self.enterContext(patch.object(db, "sh"))
        self.destination = self.root / "restored"
        self.new_ref = "refs/tags/snapshots/laptop/new"
        self.old_ref = "refs/tags/snapshots/laptop/old"

    def receipt(self, last):
        path = self.root / "machines/laptop/data-backup.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"results": {"fixture": last}}))

    def test_default_restore_falls_back_to_prior_manifest_transactionally(self):
        self.receipt({"ref": self.new_ref, "manifest": {"test": "new"},
                      "recovery_references": [{"ref": self.old_ref, "manifest": {"test": "old"}}]})
        seen = []
        def restore(manifest, destination, *args):
            seen.append(manifest["test"])
            self.assertFalse(Path(destination).exists())
            if manifest["test"] == "new":
                raise RuntimeError("synthetic unavailable/corrupt archive")
            Path(destination).mkdir()
            (Path(destination) / "verified").write_text("old payload")
        with patch.object(db.dm, "restore_manifest", side_effect=restore):
            self.assertEqual(db._cmd_restore("fixture", str(self.destination)), 0)
        self.assertEqual(seen, ["new", "old"])
        self.assertEqual((self.destination / "verified").read_text(), "old payload")
        self.shell.assert_not_called()

    def test_explicit_ref_failure_never_falls_back_to_older_snapshot(self):
        self.receipt({"ref": self.new_ref, "manifest": {"test": "new"},
                      "recovery_references": [{"ref": self.old_ref, "manifest": {"test": "old"}}]})
        with patch.object(db.dm, "restore_manifest", side_effect=RuntimeError("PRIVATE_DIAGNOSTIC")) as restore, self.assertRaises(SystemExit) as error:
            db.cmd_restore("fixture", str(self.destination), ref=self.new_ref)
        self.assertEqual(restore.call_count, 1)
        self.assertEqual(restore.call_args.args[0], {"test": "new"})
        self.assertNotIn("PRIVATE_DIAGNOSTIC", str(error.exception.code))
        self.assertFalse(self.destination.exists())
        self.shell.assert_not_called()

    def test_backend_change_preserves_default_pinned_legacy_receipt_and_prior_refs(self):
        legacy = {"repo": "legacy-org/legacy-repo", "ref": self.old_ref, "commit": "a" * 40}
        self.receipt({"status": "held", "recovery_references": [legacy]})
        with patch.object(db, "_restore_git_snapshot", return_value=0) as restore:
            self.assertEqual(db._cmd_restore("fixture", str(self.destination)), 0)
        restore.assert_called_once_with("fixture", str(self.destination), "laptop", self.old_ref, "legacy-org/legacy-repo", "a" * 40)
        self.shell.assert_not_called()

    def test_preserved_legacy_commit_must_match_before_clone(self):
        self.shell.return_value = (0, "b" * 40 + "\t" + self.old_ref, "")
        with self.assertRaises(RuntimeError):
            db._restore_git_snapshot("fixture", str(self.destination), "laptop", self.old_ref, "legacy-org/legacy-repo", "a" * 40)
        self.assertEqual(self.shell.call_count, 1)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
