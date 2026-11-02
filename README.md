# RedApi

A small FastAPI service that re-implements, approximately, how
[Redlib](https://github.com/redlib-org/redlib) talks to Reddit - so you get a
REST API that serves Reddit data the way Redlib's own backend does, instead of
hitting Reddit's official rate-limited public API directly.

## How Redlib actually does it (and what this copies)

Per Redlib's own README and the maintainer's public write-up:

1. **OAuth "installed_client" token spoofing** - Reddit's official Android app
   supports anonymous/logged-out browsing via the
   `https://oauth.reddit.com/grants/installed_client` grant. It needs no
   username/password, just a public Android `client_id` + a random
   `device_id`. Using this grant makes Reddit treat your traffic like the real
   app, which gets a much higher rate limit than a generic script hitting
   `reddit.com/.json` cold.
2. **Token refresh every ~24h**, matching the real app's cadence, instead of
   re-authenticating per request.
3. **Header mimicking** - Android-shaped `User-Agent` on every call.
4. **Fallbacks** when something breaks: retry with backoff on 429/5xx,
   force-refresh the token on 401/403, rotate to another client_id if one's
   blocked, and if OAuth itself is unreachable, drop down to the public
   unauthenticated `*.json` endpoints on `www.reddit.com` then
   `old.reddit.com`.

This project implements that same chain in Python:

```
reddit_client.py
 ├── _fetch_token()        # installed_client OAuth grant
 ├── _get_token()           # cache + refresh + client_id rotation
 ├── _request_oauth()       # oauth.reddit.com, retries once on 401/403
 ├── _request_public_json() # unauthenticated *.json fallback
 └── get()                  # walks FALLBACK_TIERS, retries, stale-cache last resort
```

It's an **approximation**, not a port of Redlib's Rust source - the request
shape and fallback *strategy* match, the exact header set and edge-case
handling don't claim byte-for-byte parity.

## Setup

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

## Endpoints

| Route | Mirrors |
|---|---|
| `GET /r/{subreddit}/{sort}` | `reddit.com/r/{sub}/{hot,new,top,rising,controversial}.json` |
| `GET /r/{subreddit}/about` | subreddit info |
| `GET /r/{subreddit}/comments/{post_id}` | post + comment tree |
| `GET /comments/{post_id}` | same, short form |
| `GET /user/{username}/about` | user profile |
| `GET /user/{username}/{sort}` | overview/submitted/comments/upvoted/gilded |
| `GET /search?q=...&subreddit=...` | search, optionally scoped to a sub |
| `GET /best` | r/popular-style front page |
| `GET /raw/{path}` | escape hatch - any reddit.com JSON path through the same fallback chain |

```bash
curl http://localhost:8080/r/python/hot
curl http://localhost:8080/r/python/comments/abc123
curl "http://localhost:8080/search?q=rust&subreddit=programming"
curl "http://localhost:8080/raw/r/python/top?t=week"
```

## Things to know before you run this for real

- **`ANDROID_CLIENT_IDS` in `config.py`** holds the historical public Android
  client_id. Reddit rotates/blocks these from time to time - if token
  requests start failing with 400/401, you'll need to capture a current one
  yourself (mitmproxy against the real app, same technique the Redlib
  maintainer used) or pull whatever Redlib's `src/client.rs` currently uses,
  and add it to the list. Multiple entries let the rotation logic actually
  do something when one gets blocked.
- **Cache is in-memory and per-process** - fine for a single `uvicorn`
  worker, not for a multi-process/multi-machine deployment as-is.
- **ToS** - this hits Reddit the same way Redlib does. Whether that's fine
  for your use case (personal self-hosted frontend vs. high-volume scraping)
  is worth checking against [Reddit's terms](https://www.redditinc.com/policies)
  yourself.

## Files

- `config.py` - client_id pool, UA pools, fallback tier order, timing constants
- `cache.py` - tiny TTL cache (fresh + stale reads)
- `reddit_client.py` - the actual client + fallback chain
- `main.py` - FastAPI routes

## DemandLark fork notes (2026-10-23)

Reddit closed logged-out access on 2026-10-22: public `*.json` answers 403 or redirects to a login page, and reads made
with the old anonymous `installed_client` token, or with a non-browser TLS handshake, answer 403 "blocked by network
security". Following the note in `config.py` (take the current approach from Redlib), this fork:

- gets the logged-out token from the Android app's loid endpoint (`/auth/v2/oauth/access-token/loid`), as Redlib's
  `MobileSpoofAuth` does, and sends back the `x-reddit-loid` / `x-reddit-session` headers and the app's device headers;
- talks to Reddit through `curl_cffi` with a browser TLS profile (Redlib uses `wreq` emulation for the same reason);
- encodes the comments separator (`/r/sub%2Fcomments/id`) like Redlib.

Verified 2026-10-23: listings, search, subreddit about/rules, `/by_id` and `/duplicates` answer 200. Comment threads
still answer 403. For development and proof-of-concept use only: low request volume, to be replaced by the official
Reddit Data API before any launch.

### Comment threads through GraphQL (2026-11-02)

Reddit's edge refuses every comments JSON route for logged-out tokens ("blocked by network security": `/comments/…`,
`/r/<sub>/comments/…`, `/api/info`, `/api/morechildren`); the `%2F` separator trick stopped working on 2026-10-22.
Comments routes (`/r/{sub}/comments/{id}`, `/comments/{id}`, and `/raw/…/comments/{id}/{slug}/{comment}.json`) now
read the post from `/by_id/t3_{id}` and the comments from the Android app's GraphQL API (`gql-fed.reddit.com`,
persisted query `PostComments`, see `reddit_gql.py`), converted to Reddit's JSON shape `[postListing, commentListing]`
(t1 things with nested `replies` Listings and `more` stubs). `sort`, `limit` (≤ 200), `depth` (≤ 10) and a focused
comment (path or `?comment=`) are honoured; `context` is not. When GraphQL fails the old JSON route is tried as
before. A missing post answers 404. Comments reads never count toward the self-heal restart.
