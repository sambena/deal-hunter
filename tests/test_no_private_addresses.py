"""The repository is public: no private network address of a real install may appear in it (#498).

Generic examples (in docs and test fixtures) are allowed by exact value; anything else in the private ranges fails.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "data", "__pycache__"}
TEXT = {".py", ".js", ".html", ".css", ".md", ".json", ".txt", ".bat", ".cmd", ".sh", ".toml", ".cfg", ".ini", ".yml",
        ".yaml"}
PRIVATE = re.compile(r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b")
EXAMPLES = {"192.168.1.10", "192.168.1.20", "192.168.1.30", "192.168.1.50", "10.0.0.5"}
# The private ranges themselves, as the new app's "public address" rule names them (site/src/logic/ip-ranges.ts).
EXAMPLES |= {"10.0.0.0", "172.16.0.0", "192.168.0.0"}


def files():
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix in TEXT and not SKIP_DIRS & set(path.relative_to(ROOT).parts):
            yield path


class NoPrivateAddressTest(unittest.TestCase):
    def test_only_generic_examples(self):
        found = []
        for path in files():
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                found += [f"{path.relative_to(ROOT)}:{n}" for m in PRIVATE.finditer(line) if m.group(0) not in EXAMPLES]
        self.assertEqual(found, [], "private address that isn't a generic example (use one from EXAMPLES)")


if __name__ == "__main__":
    unittest.main()
