#!/usr/bin/env python3
"""backup.classify treats HALO-AGENCY as an owned account (ADR 0007 amendment, 3 Oct): a private HALO-AGENCY repo gets
backup refs in itself, the CRM is never copied, a public one stays an overlay bundle, a deleted one is never recreated.

  python3 -m unittest tests/test_backup_owned.py -v
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import backup

GH = {"siso-estate": {"name": "siso-estate", "nameWithOwner": "sisodias/siso-estate", "visibility": "PRIVATE"},
      "halo-agency/halo-streaming": {"name": "halo-streaming", "nameWithOwner": "HALO-AGENCY/halo-streaming", "visibility": "PRIVATE"},
      "halo-agency/halo-docs": {"name": "halo-docs", "nameWithOwner": "HALO-AGENCY/halo-docs", "visibility": "PUBLIC"},
      "halo-agency/halocrm": {"name": "halocrm", "nameWithOwner": "HALO-AGENCY/halocrm", "visibility": "PRIVATE"}}


def rec(path, owner, repo, dirty=1):
    url = f"https://github.com/{owner}/{repo}.git"
    return {"path": os.path.join(backup.WS, path), "remotes": {"origin": url}, "head": "abc", "dirty": dirty,
            "github": [{"owner": owner, "repo": repo, "remote": "origin"}]}


def classify(r):
    return backup.classify(r, {r["path"]: r}, GH)


class Owned(unittest.TestCase):
    def test_private_halo_agency_repo_gets_backup_refs_in_itself(self):
        c = classify(rec("SISO_Agency/partners/halo/oracle/core", "HALO-AGENCY", "halo-streaming"))
        self.assertEqual((c["action"], c["target"], c["visibility"]), ("own-private", "HALO-AGENCY/halo-streaming", "private"))
        self.assertEqual(c["live_remotes"], ["origin"])
        c = classify(rec("SISO_Agency/partners/halo/oracle/core", "HALO-AGENCY", "halo-streaming", dirty=0))
        self.assertEqual(c["action"], "clean")

    def test_sisodias_unchanged(self):
        c = classify(rec("SISO_Agents/siso-estate", "sisodias", "siso-estate"))
        self.assertEqual((c["action"], c["target"]), ("own-private", "sisodias/siso-estate"))

    def test_crm_is_never_copied_wherever_it_sits(self):
        for owner in ("HALO-AGENCY", "camronkellman"):
            c = classify(rec("_inbox/some-crm-clone", owner, "halocrm"))      # outside every HALO path
            self.assertEqual(c["action"], "skip", owner)
        self.assertEqual(classify(rec("_data/worktrees/halocrm/x", "HALO-AGENCY", "halocrm"))["action"], "skip")

    def test_public_halo_agency_repo_stays_an_overlay(self):
        c = classify(rec("_data/worktrees/halo-docs/d", "HALO-AGENCY", "halo-docs"))
        self.assertEqual((c["action"], c["target"], c["visibility"]), ("overlay", "HALO-AGENCY/halo-docs", "public"))

    def test_deleted_halo_agency_repo_is_bundled_never_recreated(self):
        c = classify(rec("_data/worktrees/gone/x", "HALO-AGENCY", "gone-repo"))
        self.assertEqual((c["action"], c["visibility"]), ("overlay", "deleted-remote"))
        self.assertIn("HALO-AGENCY/gone-repo", c["why"])
        self.assertEqual(c["live_remotes"], [])

    def test_resolve_is_case_insensitive_and_owner_scoped(self):
        self.assertEqual(backup.resolve_github("halo-agency", "Halo-Streaming", GH)[0], "HALO-AGENCY/halo-streaming")
        self.assertEqual(backup.resolve_github("sisodias", "halo-streaming", GH), (None, None))


if __name__ == "__main__":
    unittest.main()
