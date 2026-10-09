"""The legend's partners in code: the same answer before and after a partner move (docs/LEGEND.md §3)."""
import importlib.util, os, sys, unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import legend


def mod(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Legend(unittest.TestCase):
    def test_partner_of_before_and_after(self):
        for path, want in [("HALO_Agency/crm/repo", ("halo", None)), ("SISO_Agency/partners/halo/crm/repo", ("halo", None)),
                           ("HALO_Agency/collegebesties", ("halo", "collegebesties")),
                           ("SISO_Agency/partners/halo/clients/collegebesties", ("halo", "collegebesties")),
                           ("SISO_Agency/partners/fahmy/clients/melanotresses/code/site", ("fahmy", "melanotresses")),
                           ("SISO_Agency/partners/fahmy/bykonzyard", ("fahmy", None)),
                           ("SISO_Agency/partners/fahmy/clients/bykonz", ("fahmy", "bykonz")),
                           ("SISO_Agency/clients/lumelle", None), ("SISO_Agency/partners/halo/oracle/core", None)]:
            self.assertEqual(legend.partner_of(path), want, path)

    def test_register_places_partners(self):
        place = mod("register").place
        self.assertEqual(place("SISO_Agency/partners/halo")[2], "agency/partners/halo")
        self.assertEqual(place("SISO_Agency/partners/halo/crm/repo")[2], "agency/partners/halo/crm")
        self.assertEqual(place("SISO_Agency/partners/fahmy/clients/melanotresses/code/site")[2],
                         "agency/partners/fahmy/clients/melanotresses")
        self.assertEqual(place("SISO_Agency/clients/lumelle/code/x")[2], "agency/clients/lumelle")

    def test_halo_never_copied_at_its_new_home(self):
        backup, houses = mod("backup"), mod("houses")
        for p in ("/SISO_Agency/partners/halo/crm/repo/x.ts", "/HALO_Agency/crm/repo/x.ts"):
            self.assertTrue(backup.NEVER_COPY_PATHS.search(p), p)
            self.assertTrue(houses.HALO.search(p), p)
        self.assertFalse(backup.NEVER_COPY_PATHS.search("/SISO_Agency/partners/fahmy/crm/x"))


if __name__ == "__main__":
    unittest.main()
