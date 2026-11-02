"""
Comment threads from Reddit's GraphQL API (gql-fed.reddit.com), the API the Android app reads comments with.

Since 2026-10-20 Reddit's edge refuses the comments JSON routes (/r/<sub>/comments/<id>, /comments/<id>, /api/info,
/api/morechildren) for logged-out tokens with its "blocked by network security" page; the %2F-encoded separator Redlib
used as a workaround was blocked too on 2026-10-22. Listings, search, /about and /by_id still work with the same token,
and so does GraphQL's PostComments persisted query (the approach of polak14/redlib, src/gql.rs).

The answer keeps the shape of Reddit's comments JSON, [postListing, commentListing]: the post comes from /by_id (the JSON
API), the comments are converted from GraphQL's comment forest into t1 things with nested `replies` listings and `more`
stubs. This module is pure (no I/O) so it can be tested on its own; reddit_client.py does the requests.
"""

import re
from datetime import datetime
from typing import Any, Optional

GQL_URL = "https://gql-fed.reddit.com/"
# Persisted query id from the Android app (Reddit refuses raw query text). Source: polak14/redlib src/gql.rs, which
# cites github.com/Amqx/reddit-gql src/operations.ts and the APK's Apollo classes. If Reddit retires it, the answer is an
# error (PersistedQueryNotFound) and comments reads fail as before; listings are unaffected.
POST_COMMENTS_HASH = "314f29c2494fd78c7844ef2c6b09acb4bef5b37ab50b4ddc01691d6d5c8433f8"
MAX_COUNT = 200  # GraphQL returns at most 200 comments per request, filled breadth-first
MAX_DEPTH = 10

_SORTS = {
    "confidence": "CONFIDENCE",
    "best": "CONFIDENCE",
    "top": "TOP",
    "new": "NEW",
    "controversial": "CONTROVERSIAL",
    "old": "OLD",
    "qa": "QA",
}

_ID = r"[a-z0-9]{1,16}"
# /r/<sub>/comments/<id>[/<slug>[/<comment>]] and /comments/<id>[/<slug>[/<comment>]], with or without .json / a slash.
_THREAD = re.compile(
    rf"^(?:/r/(?P<sub>[A-Za-z0-9_]{{1,64}}))?/comments/(?P<post>{_ID})(?:/(?P<slug>[^/]*)(?:/(?P<comment>{_ID}))?)?/?$",
    re.IGNORECASE,
)


def parse_thread_path(path: str) -> Optional[dict]:
    """The post id (and focused comment id) of a comments route, or None for any other path."""
    base = path.split("?", 1)[0]
    if base.endswith(".json"):
        base = base[: -len(".json")]
    match = _THREAD.match(base)
    if not match:
        return None
    return {
        "subreddit": match.group("sub"),
        "post_id": match.group("post").lower(),
        "comment_id": (match.group("comment") or "").lower() or None,
    }


def _int_param(params: dict, name: str, default: int, upper: int) -> int:
    try:
        value = int(params.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, upper))


def variables(post_id: str, comment_id: Optional[str], params: dict) -> dict:
    """PostComments variables for the comments JSON route's query: sort, limit (count) and depth (maxDepth); a comment
    in the path (or ?comment=) focuses that comment's thread, as the JSON route's comment permalinks do."""
    sort = _SORTS.get(str(params.get("sort") or "confidence").lower(), "CONFIDENCE")
    focused = comment_id or str(params.get("comment") or "").lower().removeprefix("t1_") or None
    result: dict[str, Any] = {
        "id": f"t3_{post_id}",
        "sortType": sort,
        "count": _int_param(params, "limit", MAX_COUNT, MAX_COUNT),
        "maxDepth": _int_param(params, "depth", MAX_DEPTH, MAX_DEPTH),
        "includeConsistencyToken": False,
        "includePremiumAvatarTreatment": False,
    }
    if focused:
        result["focusedCommentContext"] = {"commentId": f"t1_{focused}", "isFocusedThreadOnly": True}
    return result


def request_body(variables_: dict) -> dict:
    return {
        "operationName": "PostComments",
        "variables": variables_,
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": POST_COMMENTS_HASH}},
    }


def comment_trees(response: Any) -> Optional[list]:
    """The forest's entries (depth-first, each with depth/parentId and a node or a `more`), or None when the answer has
    no comment forest (an error, a missing post, or a focused comment Reddit can't find: DEPENDENCY_FAILURE)."""
    if not isinstance(response, dict):
        return None
    post = (response.get("data") or {}).get("postInfoById")
    if not isinstance(post, dict):
        return None
    forest = post.get("commentForest")
    if not isinstance(forest, dict) or not isinstance(forest.get("trees"), list):
        return None
    return forest["trees"]


def _timestamp(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
        except ValueError:
            return None


def listing(children: list) -> dict:
    return {"kind": "Listing", "data": {"after": None, "before": None, "dist": None, "children": children}}


def _thing(entry: dict, post: dict) -> Optional[dict]:
    """One forest entry as a thing of the comments JSON: a t1 comment or a `more` stub."""
    post_name = f"t3_{post.get('id', '')}"
    parent = entry.get("parentId") or post_name
    depth = int(entry.get("depth") or 0)

    more = entry.get("more")
    if isinstance(more, dict):
        count = int(more.get("count") or 0)
        return {
            "kind": "more",
            "data": {"count": count, "name": "t1__", "id": "_", "parent_id": parent, "depth": depth, "children": []},
        }

    node = entry.get("node")
    if not isinstance(node, dict):
        return None
    fullname = str(node.get("id") or "")
    comment_id = fullname.removeprefix("t1_")
    author = ((node.get("authorInfo") or {}).get("name")) or "[deleted]"
    content = node.get("content") or {}
    body = content.get("markdown") or ""
    removed_by = node.get("removedByCategory")
    if node.get("__typename") == "DeletedComment":
        author, body = "[deleted]", "[deleted]"
    elif node.get("isAdminTakedown"):
        body, removed_by = "[ Removed by Reddit ]", removed_by or "reddit"
    elif node.get("isRemoved"):
        author, body = "[deleted]", "[removed]"

    edited = _timestamp(node.get("editedAt"))
    data = {
        "id": comment_id,
        "name": fullname or None,
        "parent_id": parent,
        "link_id": post_name,
        "subreddit": post.get("subreddit"),
        "subreddit_id": post.get("subreddit_id"),
        "author": author,
        "body": body,
        "score": int(node.get("score") or 0),
        "ups": int(node.get("score") or 0),
        "score_hidden": bool(node.get("isScoreHidden")),
        "created_utc": _timestamp(node.get("createdAt")) or 0,
        "edited": edited if edited is not None else False,
        "is_submitter": bool(node.get("isOP")),
        "stickied": bool(node.get("isStickied")),
        "locked": bool(node.get("isLocked")),
        "archived": bool(node.get("isArchived")),
        "distinguished": (node.get("distinguishedAs") or "").lower() or None,
        "removed_by_category": removed_by,
        "permalink": node.get("permalink"),
        "depth": depth,
        "replies": "",
    }
    if content.get("html"):
        data["body_html"] = content["html"]
    return {"kind": "t1", "data": data}


def comments_listing(trees: list, post: dict) -> dict:
    """Nest the depth-first forest into the comments JSON's tree: each comment's `replies` is a Listing (or "" when it
    has none), `more` stubs sit where Reddit cut the tree. Goes by depth, not parent ids (deleted comments have none).
    A focused read starts at the focused comment (depth 0)."""
    top: list = []
    open_: list[tuple[int, dict]] = []  # the chain of t1 comments the next entry may be a reply to

    def attach(thing: dict) -> None:
        if not open_:
            top.append(thing)
            return
        parent = open_[-1][1]["data"]
        if not isinstance(parent["replies"], dict):
            parent["replies"] = listing([])
        parent["replies"]["data"]["children"].append(thing)

    for entry in trees:
        if not isinstance(entry, dict):
            continue
        thing = _thing(entry, post)
        if thing is None:
            continue
        depth = int(entry.get("depth") or 0)
        while open_ and open_[-1][0] >= depth:
            attach(open_.pop()[1])
        if thing["kind"] == "more":
            attach(thing)
        else:
            open_.append((depth, thing))
    while open_:
        attach(open_.pop()[1])
    return listing(top)
