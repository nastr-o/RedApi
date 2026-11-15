"""
Reddit's own web pages (shreddit), read as a signed-in browser, converted to the JSON API's shape.

Why (2026-11-15): Reddit refuses every post-reading JSON/app route (listings, search, /by_id, comments, GraphQL) for
logged-out *and* logged-in app tokens alike ("blocked by network security"), while the normal website still serves a
signed-in browser. So listings, single posts and threads are read from the pages a browser loads:

- community listing:  /r/{sub}/{sort}/  (3 posts) then /svc/shreddit/community-more-posts/{sort}/?after=… (~25 each)
- one post + comments: the post page /r/{sub}/comments/{id}/ (the post, and the first comment batch)
- search:              /search/?q=…&type=posts  (7 results per page, thin: id, title, author, community)

Pure functions only (no I/O), so they are tested against saved pages. Every field the DemandLark scanner reads
(`server/src/redapi/schema.ts`) is filled when the page has it; flags absent from the page are false.
"""

from __future__ import annotations

import base64
import html as htmllib
import json
import re
import urllib.parse
from datetime import datetime
from typing import Any, Optional

BASE = "https://www.reddit.com"

_POST_TAG = re.compile(r"<shreddit-post\s[^>]*>")
_COMMENT_TAG = re.compile(r"<shreddit-comment\s[^>]*>")
_ATTR = re.compile(r'\s([A-Za-z][A-Za-z0-9\-]*)(?:="([^"]*)")?')
_LISTING_PATH = re.compile(r"^/r/([A-Za-z0-9_]+)/(new|top|hot|rising)\.json$")
_TIME_RANGES = {"hour", "day", "week", "month", "year", "all"}


def attrs(tag: str) -> dict[str, str]:
    """Attributes of one start tag, values unescaped; a bare attribute (e.g. `nsfw`) maps to ""."""
    return {name: htmllib.unescape(value or "") for name, value in _ATTR.findall(tag)}


def _epoch(stamp: str) -> float:
    """'2026-11-15T16:30:53.941000+0000' → seconds since the epoch (0 when missing or unreadable)."""
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
    except ValueError:
        try:
            return datetime.fromisoformat(stamp.replace("+0000", "+00:00")).timestamp()
        except ValueError:
            return 0.0


def _int(value: Optional[str]) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def _balanced_div(page: str, start: int) -> str:
    """Inner HTML of the <div> whose start tag begins at `start`."""
    open_end = page.find(">", start)
    if open_end < 0:
        return ""
    depth, pos = 1, open_end + 1
    for m in re.finditer(r"<(/?)div\b", page[pos:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return page[pos:pos + m.start()]
    return page[pos:]


def html_to_text(fragment: str) -> str:
    """Reddit's rendered markdown → plain text close to the API's selftext: paragraphs and list items on their own
    lines, links keep their words, entities unescaped, whitespace collapsed."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|li|h[1-6]|blockquote|pre|tr)>", "\n\n", text, flags=re.I)
    text = re.sub(r"<li\b[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmllib.unescape(text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip().replace("\n\n\n", "\n\n")


def _body(page: str, fullname: str, kind: str) -> str:
    """Body text of a post (`kind`='post') or comment ('comment') by its rich-text container id."""
    m = re.search(rf'<div[^>]*\bid="{re.escape(fullname)}-{kind}-rtjson-content"', page)
    return html_to_text(_balanced_div(page, m.start())) if m else ""


def post_from_tag(tag: str, page: str) -> Optional[dict]:
    """One <shreddit-post> as a JSON API t3 `data` dict."""
    a = attrs(tag)
    fullname = a.get("id", "")
    if not fullname.startswith("t3_"):
        return None
    post_type = a.get("post-type", "")
    permalink = a.get("permalink", "")
    subreddit = a.get("subreddit-name") or a.get("subreddit-prefixed-name", "").removeprefix("r/")
    verdict = a.get("moderation-verdict", "")
    return {
        "id": fullname[3:],
        "name": fullname,
        "title": a.get("post-title", ""),
        "subreddit": subreddit,
        "subreddit_id": a.get("subreddit-id") or None,
        "subreddit_name_prefixed": f"r/{subreddit}" if subreddit else None,
        "author": a.get("author") or "[deleted]",
        "author_fullname": a.get("author-id") or None,
        "permalink": permalink,
        "url": a.get("content-href") or (BASE + permalink if permalink else None),
        "selftext": _body(page, fullname, "post"),
        "created_utc": _epoch(a.get("created-timestamp", "")),
        "score": _int(a.get("score")),
        "upvote_ratio": float(a.get("upvote-ratio") or 0) or None,
        "num_comments": _int(a.get("comment-count")),
        "domain": a.get("domain") or None,
        "is_self": post_type in ("text", "self") or (a.get("domain", "").startswith("self.")),
        "over_18": "nsfw" in a or "is-nsfw" in a,
        "spoiler": "spoiler" in a,
        "locked": "locked" in a or "is-locked" in a,
        "archived": "archived" in a or "is-archived" in a,
        "stickied": "stickied" in a or "pinned" in a,
        "removed_by_category": verdict.lower() if verdict.lower() in ("removed", "mod_removed", "admin_removed") else None,
        "distinguished": a.get("distinguished") or None,
        "crosspost_parent": a.get("crosspost-parent-id") or ("crosspost" if post_type == "crosspost" else None),
        "link_flair_text": None,
        "post_hint": post_type or None,
    }


def posts(page: str) -> list[dict]:
    """Every post on a page, in order, once each (promoted posts use another tag and are skipped)."""
    seen: set[str] = set()
    out: list[dict] = []
    for m in _POST_TAG.finditer(page):
        post = post_from_tag(m.group(0), page)
        if post and post["name"] not in seen:
            seen.add(post["name"])
            out.append(post)
    return out


def more_cursor(page: str) -> Optional[str]:
    """The cursor the page's "load more" uses (`more-posts-cursor`, base64 of the last post's fullname)."""
    found = re.findall(r'more-posts-cursor="([^"]+)"', page)
    return found[-1] if found else None


def cursor_for(fullname: str) -> str:
    """The JSON API's `after` (a t3_ fullname) as the website's cursor."""
    return base64.b64encode(fullname.encode()).decode()


def listing(children: list[dict], after: Optional[str]) -> dict:
    return {
        "kind": "Listing",
        "data": {"after": after, "before": None, "dist": len(children),
                 "children": [{"kind": "t3", "data": c} for c in children]},
    }


def parse_listing_path(path: str) -> Optional[tuple[str, str]]:
    """'/r/SaaS/new.json' → ('SaaS', 'new'); anything else → None."""
    m = _LISTING_PATH.match(path)
    return (m.group(1), m.group(2)) if m else None


def listing_first_url(subreddit: str, sort: str, t: Optional[str]) -> tuple[str, dict]:
    params = {"t": t} if sort == "top" and t in _TIME_RANGES else {}
    return f"{BASE}/r/{subreddit}/{sort}/", params


def listing_more_url(subreddit: str, sort: str, t: Optional[str], cursor: str) -> tuple[str, dict]:
    """The page's own "load more" request: the cursor padded to whole base64, `t` upper-case (DAY unless top)."""
    cursor = cursor + "=" * (-len(cursor) % 4)
    params = {"after": cursor, "name": subreddit, "t": t.upper() if sort == "top" and t in _TIME_RANGES else "DAY"}
    return f"{BASE}/svc/shreddit/community-more-posts/{sort}/", params


# ------------------------------------------------------------------ threads

def comments(page: str, post_id: str) -> list[dict]:
    """The page's comments as a nested JSON API comment forest (t1 things with `replies` Listings)."""
    flat: list[dict] = []
    for m in _COMMENT_TAG.finditer(page):
        a = attrs(m.group(0))
        thing = a.get("thingId", "")
        if not thing.startswith("t1_"):
            continue
        flat.append({
            "id": thing[3:],
            "name": thing,
            "parent_id": a.get("parentId") or f"t3_{post_id}",
            "link_id": a.get("postId") or f"t3_{post_id}",
            "author": a.get("author") or "[deleted]",
            "body": _body(page, thing, "comment"),
            "score": _int(a.get("score")),
            "created_utc": _epoch(a.get("created", "")),
            "depth": _int(a.get("depth")),
            "permalink": a.get("permalink") or None,
            "replies": "",
        })
    by_name = {c["name"]: c for c in flat}
    roots: list[dict] = []
    for c in flat:
        parent = by_name.get(c["parent_id"])
        if parent is None:
            roots.append(c)
            continue
        if not isinstance(parent["replies"], dict):
            parent["replies"] = {"kind": "Listing", "data": {"after": None, "children": []}}
        parent["replies"]["data"]["children"].append({"kind": "t1", "data": c})
    return [{"kind": "t1", "data": c} for c in roots]


def thread(page: str, post_id: str) -> Optional[list]:
    """A post page as the JSON API's [postListing, commentListing]; None when the page has no such post."""
    post = next((p for p in posts(page) if p["id"] == post_id), None)
    if post is None:
        return None
    forest = comments(page, post_id)
    return [listing([post], None), {"kind": "Listing", "data": {"after": None, "children": forest}}]


def post_url(post_id: str, comment_id: Optional[str] = None, subreddit: Optional[str] = None) -> str:
    """A post page. `/r/<sub>/comments/<id>/` without the title slug serves an empty shell, so the short
    `/comments/<id>/` (which redirects to the full URL) is used; a focused comment needs the community."""
    if comment_id and subreddit:
        return f"{BASE}/r/{subreddit}/comments/{post_id}/comment/{comment_id}/"
    if comment_id:
        return f"{BASE}/comments/{post_id}/comment/{comment_id}/"
    return f"{BASE}/comments/{post_id}/"


# ------------------------------------------------------------------- search

def search_hits(page: str) -> list[dict]:
    """Search results: {id, title, author, subreddit, nsfw} from each result's tracking data, once each, in order."""
    seen: set[str] = set()
    out: list[dict] = []
    for raw in re.findall(r'data-faceplate-tracking-context="([^"]+)"', page):
        try:
            ctx = json.loads(htmllib.unescape(raw))
        except ValueError:
            continue
        post = ctx.get("post") or {}
        fullname = post.get("id") or ""
        if not fullname.startswith("t3_") or fullname in seen or not ctx.get("subreddit"):
            continue
        seen.add(fullname)
        out.append({
            "id": fullname[3:],
            "title": post.get("title") or "",
            "author": (ctx.get("profile") or {}).get("name") or "[deleted]",
            "subreddit": ctx["subreddit"].get("name") or "",
            "nsfw": bool(post.get("nsfw") or ctx["subreddit"].get("nsfw")),
        })
    return out


def search_cursor(page: str) -> Optional[str]:
    found = re.findall(r'/svc/shreddit/search/?\?[^"]*?cursor=([A-Za-z0-9_%=\-]+)', page)
    return urllib.parse.unquote(htmllib.unescape(found[-1])) if found else None  # sent again as a query param


def search_url(q: str, sort: str, t: Optional[str], subreddit: Optional[str], cursor: Optional[str]) -> tuple[str, dict]:
    params: dict[str, Any] = {"q": q, "type": "posts", "sort": sort or "relevance"}
    if t in _TIME_RANGES:
        params["t"] = t
    if cursor:
        params["cursor"] = cursor
        return f"{BASE}/svc/shreddit/search/" if not subreddit else f"{BASE}/svc/shreddit/r/{subreddit}/search/", params
    if subreddit:
        params["restrict_sr"] = "1"
        return f"{BASE}/r/{subreddit}/search/", params
    return f"{BASE}/search/", params
