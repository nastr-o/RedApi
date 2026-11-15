"""Tests for reddit_web against small hand-written pages shaped like Reddit's (shreddit) markup.
Run: python -m unittest discover -s tests (from the repo root)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reddit_web as w  # noqa: E402

POST = (
    '<shreddit-post permalink="/r/SaaS/comments/abc123/need_a_tool/" content-href="https://www.reddit.com/r/SaaS/comments/abc123/need_a_tool/" '
    'comment-count="7" created-timestamp="2026-11-15T16:30:53.941000+0000" domain="self.SaaS" id="t3_abc123" '
    'post-title="Need a tool &amp; advice?" post-type="text" score="12" upvote-ratio="0.93" subreddit-id="t5_2qkq6" '
    'subreddit-prefixed-name="r/SaaS" author-id="t2_x" author="alice" subreddit-name="SaaS" more-posts-cursor="dDNfYWJjMTIz">'
    '<div slot="text-body"><div class="md" id="t3_abc123-post-rtjson-content"><p>First line with <a href="#">a link</a>.</p>'
    '<div><p>Nested &amp; para</p></div></div></div></shreddit-post>'
    '<shreddit-post id="t3_def456" post-title="Pic" post-type="image" nsfw locked author="bob" subreddit-name="SaaS" '
    'created-timestamp="2026-11-14T10:00:00.000000+0000" score="3" comment-count="0" permalink="/r/SaaS/comments/def456/pic/"></shreddit-post>'
    '<shreddit-post-flair id="not-a-post"></shreddit-post-flair>'
)

THREAD = POST + (
    '<shreddit-comment created="2026-11-15T16:41:09.814000+0000" author="carol" thingId="t1_c1" depth="0" '
    'permalink="/r/SaaS/comments/abc123/comment/c1/" score="4" postId="t3_abc123" parentId="t3_abc123">'
    '<div class="md" id="t1_c1-comment-rtjson-content" slot="comment"><p>Try X.</p></div>'
    '<shreddit-comment created="2026-11-15T17:00:00.000000+0000" author="alice" thingId="t1_c2" depth="1" score="1" '
    'postId="t3_abc123" parentId="t1_c1"><div id="t1_c2-comment-rtjson-content" slot="comment"><p>Thanks!</p></div>'
    '</shreddit-comment></shreddit-comment>'
)

SEARCH = (
    '<div data-faceplate-tracking-context="{&quot;post&quot;:{&quot;id&quot;:&quot;t3_abc123&quot;,&quot;nsfw&quot;:false,'
    '&quot;title&quot;:&quot;Need a tool&quot;},&quot;profile&quot;:{&quot;name&quot;:&quot;alice&quot;},'
    '&quot;subreddit&quot;:{&quot;name&quot;:&quot;SaaS&quot;,&quot;nsfw&quot;:false}}"></div>'
    '<div data-faceplate-tracking-context="{&quot;post&quot;:{&quot;id&quot;:&quot;t3_abc123&quot;},&quot;subreddit&quot;:{&quot;name&quot;:&quot;SaaS&quot;}}"></div>'
    '<faceplate-partial src="/svc/shreddit/search/?q=x&amp;type=posts&amp;cursor=eyJh%3D%3D"></faceplate-partial>'
)


class PostsTest(unittest.TestCase):
    def test_fields(self):
        posts = w.posts(POST)
        self.assertEqual([p["id"] for p in posts], ["abc123", "def456"])
        p = posts[0]
        self.assertEqual(p["name"], "t3_abc123")
        self.assertEqual(p["title"], "Need a tool & advice?")
        self.assertEqual((p["subreddit"], p["author"], p["score"], p["num_comments"]), ("SaaS", "alice", 12, 7))
        self.assertAlmostEqual(p["created_utc"], 1794760253.941, places=2)
        self.assertTrue(p["is_self"])
        self.assertFalse(p["over_18"] or p["locked"])
        self.assertEqual(p["selftext"], "First line with a link .\n\nNested & para")

    def test_flags(self):
        pic = w.posts(POST)[1]
        self.assertTrue(pic["over_18"] and pic["locked"])
        self.assertFalse(pic["is_self"])
        self.assertEqual(pic["selftext"], "")

    def test_cursor(self):
        self.assertEqual(w.more_cursor(POST), "dDNfYWJjMTIz")
        self.assertEqual(w.cursor_for("t3_abc123"), "dDNfYWJjMTIz")
        _, params = w.listing_more_url("SaaS", "new", None, "dDNfMXh3emw0MA")
        self.assertEqual(params, {"after": "dDNfMXh3emw0MA==", "name": "SaaS", "t": "DAY"})
        _, params = w.listing_more_url("SaaS", "top", "month", "x")
        self.assertEqual(params["t"], "MONTH")

    def test_listing_path(self):
        self.assertEqual(w.parse_listing_path("/r/SaaS/new.json"), ("SaaS", "new"))
        self.assertIsNone(w.parse_listing_path("/r/SaaS/about.json"))


class ThreadTest(unittest.TestCase):
    def test_nested(self):
        post_listing, comments = w.thread(THREAD, "abc123")
        self.assertEqual(post_listing["data"]["children"][0]["data"]["id"], "abc123")
        roots = comments["data"]["children"]
        self.assertEqual(len(roots), 1)
        top = roots[0]["data"]
        self.assertEqual((top["author"], top["body"], top["score"]), ("carol", "Try X.", 4))
        reply = top["replies"]["data"]["children"][0]["data"]
        self.assertEqual((reply["author"], reply["body"], reply["parent_id"]), ("alice", "Thanks!", "t1_c1"))

    def test_missing_post(self):
        self.assertIsNone(w.thread(THREAD, "zzz"))

    def test_post_url(self):
        self.assertEqual(w.post_url("abc"), "https://www.reddit.com/comments/abc/")
        self.assertEqual(w.post_url("abc", "c1", "SaaS"), "https://www.reddit.com/r/SaaS/comments/abc/comment/c1/")


class SearchTest(unittest.TestCase):
    def test_hits_once(self):
        hits = w.search_hits(SEARCH)
        self.assertEqual(hits, [{"id": "abc123", "title": "Need a tool", "author": "alice", "subreddit": "SaaS", "nsfw": False}])

    def test_cursor_decoded(self):
        self.assertEqual(w.search_cursor(SEARCH), "eyJh==")


if __name__ == "__main__":
    unittest.main()
