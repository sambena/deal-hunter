"""Reddit rate limiting: requests are spaced out, a 429 pauses all Reddit sources, stale posts are reused."""

import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sources  # noqa: E402

RSS = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>t3_a</id><title>[GPU] RTX 3060 12GB - $229</title></entry></feed>'


class Resp:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return RSS


def too_many(req, timeout):
    raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)


@mock.patch.object(sources, "REDDIT_GAP_SECONDS", 0)
class RateLimitTest(unittest.TestCase):
    def setUp(self):
        sources._reddit_state.update(last=0.0, blocked_until=0.0)
        sources._feed_cache.clear()

    def test_429_pauses_every_reddit_source_without_retrying(self):
        calls = []
        with mock.patch.object(sources.urllib.request, "urlopen", lambda r, timeout: calls.append(1) or too_many(r, timeout)):
            with self.assertRaises(sources.SourceError):
                sources._reddit_feed("buildapcsales")
            with self.assertRaises(sources.SourceError) as cm:
                sources._reddit_feed("hardwareswap")  # different sub, same pause
        self.assertEqual(len(calls), 1)
        self.assertIn("slow down", str(cm.exception))

    def test_requests_are_spaced(self):
        sleeps = []
        with mock.patch.object(sources, "REDDIT_GAP_SECONDS", 8), \
             mock.patch.object(sources.time, "sleep", sleeps.append), \
             mock.patch.object(sources.urllib.request, "urlopen", lambda r, timeout: Resp()):
            sources._reddit_feed("a")
            sources._reddit_feed("b")
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 7)

    def test_stale_posts_are_reused_while_paused(self):
        with mock.patch.object(sources.urllib.request, "urlopen", lambda r, timeout: Resp()):
            first = sources._cached_feed("buildapcsales")
        sources._feed_cache["buildapcsales"] = (0.0, first)  # make it due for a refresh
        sources._reddit_state["blocked_until"] = sources.time.time() + 600
        self.assertEqual(sources._cached_feed("buildapcsales"), first)


if __name__ == "__main__":
    unittest.main()
