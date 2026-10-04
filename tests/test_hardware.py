"""Built-in upgrade rules for older Intel desktops (LGA1155 and LGA1150)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hardware  # noqa: E402

BUTTER = {"name": "Butter", "parts": [{"category": "cpu", "model": "Intel Core i7-4790"},
                                      {"category": "ram", "model": "16GB DDR3"},
                                      {"category": "gpu", "model": "AMD Radeon RX 6500 XT"},
                                      {"category": "motherboard", "model": "MSI z87 Mpower"}]}


class OlderIntelTest(unittest.TestCase):
    def test_sockets_from_cpu_names(self):
        for cpu, socket in [("Intel Core i7-4790", "LGA1150"), ("i5-4690K", "LGA1150"), ("i7-5775C", "LGA1150"),
                            ("i5-3570K", "LGA1155"), ("Intel(R) Core(TM) i7-2600 CPU @ 3.40GHz", "LGA1155"),
                            ("i7-4960X", None), ("i7-3930K", None), ("i7-5820K", "LGA2011-3")]:
            self.assertEqual(hardware.cpu_socket(cpu), socket, cpu)

    def test_chipsets(self):
        for board, socket in [("MSI Z87 MPOWER", "LGA1150"), ("ASUS H97-PLUS", "LGA1150"), ("Gigabyte B85M-D3H", "LGA1150"),
                              ("ASRock Z77 Extreme4", "LGA1155"), ("ASUS P8P67", "LGA1155"), ("MSI H61M-P20", "LGA1155")]:
            self.assertEqual(hardware.detect_platform([{"category": "motherboard", "model": board}])[0], socket, board)

    def test_butter_gets_the_4790k_and_32gb_but_not_broadwell_on_z87(self):
        out = hardware.suggest(BUTTER)
        self.assertEqual(out["platform"], "Intel 8/9-series (LGA1150)")
        names = [s["name"] for s in out["suggestions"]]
        self.assertEqual(names, ["i7-4790K for Butter", "DDR3 4X8GB kit for Butter"])
        self.assertEqual(out["suggestions"][1]["query"], "ddr3 4x8gb|32gb")

    def test_broadwell_offered_on_z97_or_an_unknown_board(self):
        z97 = {"name": "x", "parts": [{"category": "cpu", "model": "i7-4790"}, {"category": "motherboard", "model": "ASUS Z97-A"}]}
        self.assertIn("i7-5775C for x", [s["name"] for s in hardware.suggest(z97)["suggestions"]])
        cpu_only = {"name": "x", "parts": [{"category": "cpu", "model": "i7-4790"}]}
        self.assertIn("i7-5775C for x", [s["name"] for s in hardware.suggest(cpu_only)["suggestions"]])


if __name__ == "__main__":
    unittest.main()
