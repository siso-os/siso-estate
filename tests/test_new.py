"""estate new: placement, refusals and a real birth in a temporary workspace."""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
NEW = os.path.join(os.path.dirname(HERE), "tools", "new.py")


class New(unittest.TestCase):
    def run_new(self, *args, ws=None):
        env = dict(os.environ)
        if ws:
            env["ESTATE_WS"] = ws
        return subprocess.run([sys.executable, NEW, *args], capture_output=True, text=True, env=env)

    def test_dry_run_places_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as ws:
            r = self.run_new("agency/apps/demo-app", "--kind", "app", ws=ws)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("SISO_Agency/apps/demo-app", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(ws, "SISO_Agency")))

    def test_refusals(self):
        self.assertNotEqual(self.run_new("nowhere/x").returncode, 0)                 # unknown island
        self.assertNotEqual(self.run_new("agency/demo-app").returncode, 0)          # Agency needs a district
        self.assertEqual(self.run_new("engine/siso-estate").returncode, 1)          # a second home
        self.assertEqual(self.run_new("halo/crm/crm-reports").returncode, 1)        # HALO needs the lead's go
        self.assertNotEqual(self.run_new("engine/Bad_Name").returncode, 0)          # not a repo name
        self.assertNotEqual(self.run_new("agency/clients/melanotresses/x").returncode, 0)   # Fahmy's client, not a direct one
        self.assertNotEqual(self.run_new("agency/partners/nobody/x").returncode, 0)        # not a partner in the legend
        self.assertEqual(self.run_new("agency/partners/halo/crm/crm-reports").returncode, 1)  # HALO's law follows the partner

    def test_partner_places(self):
        with tempfile.TemporaryDirectory() as ws:   # no partner home on this disk yet: the planned place
            r = self.run_new("agency/partners/fahmy/clients/newbrand", ws=ws)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("SISO_Agency/partners/fahmy/clients/newbrand", r.stdout)
            self.assertIn('"compound": "agency/partners/fahmy/clients/newbrand"', r.stdout)
            os.makedirs(os.path.join(ws, "SISO_Agency/partners/fahmy"))   # the block on disk: a birth goes inside it
            r = self.run_new("agency/partners/fahmy/bykonz-tickets", ws=ws)
            self.assertIn("SISO_Agency/partners/fahmy/bykonz-tickets", r.stdout)

    def test_real_birth(self):
        with tempfile.TemporaryDirectory() as ws:
            os.makedirs(os.path.join(ws, "SISO_Agents"))
            r = self.run_new("engine/demo-tool", "--what", "a demo", "--run", ws=ws)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            top = os.path.join(ws, "SISO_Agents", "demo-tool")
            for f in ("AGENTS.md", "CLAUDE.md", ".agents/building.json", ".agents/HANDOFF.md", ".agents/memory/MEMORY.md", ".agents/owners.log"):
                self.assertTrue(os.path.isfile(os.path.join(top, f)), f)
            card = json.load(open(os.path.join(top, ".agents", "building.json")))
            self.assertEqual((card["postcode"], card["island"], card["compound"]), ("demo-tool", "engine", "engine/demo-tool"))
            self.assertIn("born by estate new", subprocess.run(["git", "-C", top, "log", "--oneline"], capture_output=True, text=True).stdout)


if __name__ == "__main__":
    unittest.main()
