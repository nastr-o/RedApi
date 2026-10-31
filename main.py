"""
FastAPI REST surface backed by RedditClient.

Routes mirror Reddit's own JSON API shape (and therefore what Redlib calls
internally) -- so paths line up with what you'd hit on reddit.com directly,
just pointed at this server instead. Run with:

    uvicorn main:app --reload --port 8080

Then e.g.:
    curl http://localhost:8080/r/python/hot
    curl http://localhost:8080/r/python/comments/abc123
    curl http://localhost:8080/user/spez/about
    curl "http://localhost:8080/search?q=rust&subreddit=programming"
"""

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request

from reddit_client import RedditAPIError, reddit_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await reddit_client.aclose()


app = FastAPI(
    title="Reddit API Proxy (Redlib-style)",
    description=(
        "Approximate reimplementation of Redlib's spoofed-OAuth + "
        "multi-tier-fallback approach to Reddit's JSON API."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


async def _fetch(path: str, params: Optional[dict] = None):
    try:
        return await reddit_client.get(path, params)
    except RedditAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@app.api_route("/health", methods=["GET", "HEAD"])
async def health(deep: bool = False):
    if not deep:
        return {"status": "ok - checked by @exilonium"}
    # deep=1: is Reddit readable from this instance right now? One small listing read (cached 10 s).
    try:
        await reddit_client.get("/r/announcements/new", {"limit": 1})
        readable, detail = True, None
    except RedditAPIError as exc:
        readable, detail = False, str(exc)[:300]
    return {"status": "ok", "redditReadable": readable, "detail": detail, **reddit_client.status()}


@app.get("/r/{subreddit}/{sort}")
async def subreddit_listing(
    subreddit: str,
    sort: str = "hot",
    limit: int = Query(25, ge=1, le=100),
    after: Optional[str] = None,
    t: Optional[str] = Query(
        None,
        description="time filter for top/controversial: hour/day/week/month/year/all",
    ),
):
    if sort not in {"hot", "new", "top", "rising", "controversial"}:
        raise HTTPException(400, "sort must be one of hot/new/top/rising/controversial")
    params = {"limit": limit}
    if after:
        params["after"] = after
    if t:
        params["t"] = t
    return await _fetch(f"/r/{subreddit}/{sort}", params)


@app.get("/r/{subreddit}/about")
async def subreddit_about(subreddit: str):
    return await _fetch(f"/r/{subreddit}/about")


@app.get("/r/{subreddit}/comments/{post_id}")
async def post_with_comments(subreddit: str, post_id: str, sort: str = "confidence"):
    return await _fetch(f"/r/{subreddit}/comments/{post_id}", {"sort": sort})


@app.get("/comments/{post_id}")
async def post_with_comments_short(post_id: str, sort: str = "confidence"):
    return await _fetch(f"/comments/{post_id}", {"sort": sort})


@app.get("/user/{username}/about")
async def user_about(username: str):
    return await _fetch(f"/user/{username}/about")


@app.get("/user/{username}/{sort}")
async def user_listing(
    username: str,
    sort: str = "overview",
    limit: int = Query(25, ge=1, le=100),
    after: Optional[str] = None,
):
    if sort not in {"overview", "submitted", "comments", "upvoted", "gilded"}:
        raise HTTPException(400, "unsupported sort for user listing")
    params = {"limit": limit}
    if after:
        params["after"] = after
    return await _fetch(f"/user/{username}/{sort}", params)


@app.get("/search")
async def search(
    q: str,
    subreddit: Optional[str] = None,
    sort: str = "relevance",
    t: Optional[str] = Query(
        None,
        description="time filter: hour/day/week/month/year/all",
    ),
    limit: int = Query(25, ge=1, le=100),
):
    if sort not in {"relevance", "hot", "top", "new", "comments"}:
        raise HTTPException(400, "sort must be one of relevance/hot/top/new/comments")
    if t and t not in {"hour", "day", "week", "month", "year", "all"}:
        raise HTTPException(400, "t must be one of hour/day/week/month/year/all")
    params = {"q": q, "sort": sort, "limit": limit}
    if t:
        params["t"] = t
    path = f"/r/{subreddit}/search" if subreddit else "/search"
    if subreddit:
        params["restrict_sr"] = 1
    return await _fetch(path, params)


@app.get("/best")
async def best(limit: int = Query(25, ge=1, le=100), after: Optional[str] = None):
    params = {"limit": limit}
    if after:
        params["after"] = after
    return await _fetch("/best", params)


@app.get("/raw/{path:path}")
async def raw_passthrough(path: str, request: Request):
    """Escape hatch: hit any reddit.com JSON path through the same fallback
    chain, forwarding along whatever query params were given, e.g.
    /raw/r/python/top?t=week"""
    return await _fetch(f"/{path}", dict(request.query_params))
