"""Get specs: reading the commands' output (real output from Sam's machines), and the fill-in endpoint."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import db  # noqa: E402
import hardware  # noqa: E402
import specs  # noqa: E402

# Printed by specs.WINDOWS_COMMAND on Dexter.
DEXTER = """cpu: AMD Ryzen 7 3700X 8-Core Processor
board: Micro-Star International Co., Ltd. MPG B550 GAMING PLUS (MS-7C56)
system: Micro-Star International Co., Ltd. MS-7C56
ram: 8GB DDR4 3200 CMK16GX4M2B3200C16
ram: 8GB DDR4 3200 CMK16GX4M2B3200C16
gpu: NVIDIA GeForce RTX 4070 SUPER
gpu: Microsoft Basic Display Adapter
disk: 238GB Inland NVMe SSD 256GB SSD"""

# Printed by specs.LINUX_COMMAND on the Frigate box (a Dell OptiPlex).
FRIGATE = """cpu: Intel(R) Core(TM) i5-7500 CPU @ 3.40GHz
board: Dell Inc. 0XHGV1
system: Dell Inc. OptiPlex 7050
ram: 16GB total
gpu: Intel Corporation HD Graphics 630 (rev 04)
disk: 447.1G KINGSTON SA400S37480G
disk: 1.8T WDC WD20EZBX-00AYRA0"""


def parts(text):
    return [(p["category"], p["model"]) for p in specs.parse_report(text)]


class ParseTest(unittest.TestCase):
    def test_windows_output(self):
        self.assertEqual(parts(DEXTER), [
            ("cpu", "AMD Ryzen 7 3700X"),
            ("motherboard", "MSI MPG B550 GAMING PLUS (MS-7C56)"),
            ("ram", "16GB (2x8GB) DDR4-3200 CMK16GX4M2B3200C16"),
            ("gpu", "NVIDIA GeForce RTX 4070 SUPER"),  # the virtual display adapter is dropped
            ("storage", "238GB Inland NVMe SSD 256GB SSD"),
        ])

    def test_linux_output_with_an_oem_board(self):
        self.assertEqual(parts(FRIGATE), [
            ("cpu", "Intel Core i5-7500"),
            ("motherboard", "Dell OptiPlex 7050 (board 0XHGV1)"),
            ("ram", "16GB"),
            ("gpu", "Intel HD Graphics 630"),
            ("storage", "447.1GB KINGSTON SA400S37480G"),
            ("storage", "1.8TB WDC WD20EZBX-00AYRA0"),
        ])

    def test_lspci_marketing_names(self):
        got = parts("cpu: x\ngpu: NVIDIA Corporation TU116 [GeForce GTX 1660 SUPER] (rev a1)\n"
                    "gpu: Advanced Micro Devices, Inc. [AMD/ATI] Navi 48 [Radeon RX 9070 XT] (rev c0)")
        self.assertEqual(got[1:], [("gpu", "NVIDIA GeForce GTX 1660 SUPER"), ("gpu", "AMD Radeon RX 9070 XT")])

    def test_mixed_ram_and_older_powershell_media_numbers(self):
        got = dict(parts("cpu: x\nram: 16GB DDR5 6000 A\nram: 8GB DDR5 6000 B\ndisk: 932GB Samsung 870 4"))
        self.assertEqual(got["ram"], "24GB (1x16GB + 1x8GB) DDR5-6000")
        self.assertEqual(got["storage"], "932GB Samsung 870 SSD")

    def test_cpu_names_are_tidied(self):
        self.assertEqual(specs.tidy_cpu("12th Gen Intel(R) Core(TM) i7-12700"), "Intel Core i7-12700")
        self.assertEqual(specs.tidy_cpu("AMD Ryzen 5 7500X 6-Core Processor"), "AMD Ryzen 5 7500X")

    def test_what_counts_as_a_report(self):
        self.assertTrue(specs.looks_like_report(DEXTER))
        self.assertFalse(specs.looks_like_report("i7-12700, 16GB DDR4, RTX 3060 12GB"))

    def test_platform_is_found_from_raw_cpu_names(self):
        # The raw name glued "cpu" onto the suffix and read the i7-7800X as LGA1151.
        self.assertEqual(hardware.cpu_socket("Intel(R) Core(TM) i7-7800X CPU @ 3.50GHz"), "LGA2066")
        self.assertEqual(hardware.detect_platform(specs.parse_report(FRIGATE))[0], "LGA1151")

    def test_merge_keeps_other_categories(self):
        old = [{"category": "cpu", "model": "old"}, {"category": "network", "model": "Lite-On network adapter"}]
        merged = specs.merge_parts(old, [{"category": "cpu", "model": "new"}])
        self.assertEqual(merged, [{"category": "cpu", "model": "new"}, old[1]])

    def test_commands_are_single_lines(self):
        for cmd in (specs.WINDOWS_COMMAND, specs.LINUX_COMMAND):
            self.assertNotIn("\n", cmd)


class EndpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        import server
        self.server = server
        self.mid = db.save_machine({"name": "Rosie", "kind": "pc",
                                    "parts": [{"category": "network", "model": "Lite-On network adapter"}]})

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_report_fills_parts_and_keeps_the_network_card(self):
        res = self.server.fill_specs({"text": DEXTER}, {}, str(self.mid))
        self.assertEqual((res["method"], res["found"]), ("report", 5))
        cats = [p["category"] for p in db.get_machine(self.mid)["parts"]]
        self.assertEqual(cats, ["cpu", "motherboard", "ram", "gpu", "storage", "network"])

    def test_other_text_asks_for_ai(self):
        with self.assertRaises(self.server.HTTPError) as cm:
            self.server.fill_specs({"text": "i7-12700, 16GB, RTX 3060 12GB"}, {}, str(self.mid))
        self.assertEqual((cm.exception.status, str(cm.exception)), (422, "need_ai"))

    def test_ai_reads_other_text_through_the_budget_gate(self):
        settings = {**db.get_settings(), "ai_provider": "gemini", "gemini_api_key": "k", "gemini_free_tier": True}
        answer = '{"parts": [{"category": "cpu", "model": "Intel Core i7-12700"}, ' \
                 '{"category": "gpu", "model": "NVIDIA GeForce RTX 3060 12GB"}]}'
        with mock.patch.object(db, "get_settings", lambda: settings), \
             mock.patch.dict(ai.CALLERS, {"gemini": lambda *a: (answer, 100, 30, None)}):
            res = self.server.fill_specs({"text": "i7-12700, 16GB, RTX 3060 12GB", "use_ai": True}, {},
                                         str(self.mid))
        self.assertEqual((res["method"], res["found"]), ("ai", 2))
        self.assertEqual(db.query("SELECT action FROM ai_usage")[0]["action"], "specs")


if __name__ == "__main__":
    unittest.main()
