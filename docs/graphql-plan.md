# Plan: RedApi reads everything through Reddit's app API (GraphQL)

Date: 2026-11-03. Status: proposed, not built.

## Why

Since about 06:00 UTC on 2026-11-03 Reddit refuses every JSON API read for logged-out tokens: `/r/<sub>/new`,
`/search`, `/r/<sub>/about`, `/by_id` answer 302 (login redirect) or 403. It is Reddit-wide, not our instance:

- the same code run on a developer machine gets the same 403;
- public Redlib instances fail too; Redlib issue #609 (2026-10-30): "Reddit has decided to force everyone to log in";
- a fresh Render deploy (17:44) and the self-heal restarts (06:20, 06:50) changed nothing.

What still works with the same logged-out loid token: the Android app's GraphQL API at `gql-fed.reddit.com`, which
RedApi already uses for comment threads (`PostComments`, since 2026-11-02). Tested from a developer machine on
2026-11-03: `SubredditFeedElements` (r/SaaS, sort NEW) and `SearchPosts` both answered 200 with posts.

## The contract does not change

DemandLark's scanner and app keep calling the same RedApi routes and keep getting Reddit's JSON shape
(`{kind: "Listing", data: {after, children: [{kind: "t3", data: {...}}]}}`). Only how RedApi gets the data changes.
Nothing in the demandlark repo needs code changes; the submodule pointer moves to the new commit.

## Route by route

| RedApi route (what DemandLark calls) | Used for | GraphQL operation | Notes |
| --- | --- | --- | --- |
| `GET /r/{sub}/{new,top,hot}?limit&after&t` | reading communities (`fetch_subreddit_new`, `_top`, backfill) | `SubredditFeedElements` | sort and time range map to enums; `after` (a `t3_` fullname) ↔ GraphQL cursor (base64 of the fullname); `UnavailableSubreddit` → 404/403 the way the JSON API answered banned/private communities |
| `GET /search?q&sort&t&limit` | keyword search (`search_brand`) | `SearchPosts` | `t` → `time_range` filter, `nsfw` filter off; search cursors are offsets, passed through as they are |
| `GET /raw/r/{sub}/about` | community profile (`sub_maintenance`) | `SubredditInfoByName` | to a `t5` document: name, title, description, subscribers, over18, type; unavailable → same errors as before |
| `GET /raw/r/{sub}/about/rules.json` | promotion policy from the rules | `GetRules` | to `{rules: [{short_name, description, kind}]}` |
| `GET /raw/by_id/t3_{id}.json` | post stats refresh (`refresh_stats`), the post half of a thread | `PostsByIds` | up to many ids per query |
| `GET /r/{sub}/comments/{id}` and `/comments/{id}` | thread view, reply writer | `PostComments` (already) + `PostsByIds` for the post | the post read moves from `/by_id` to `PostsByIds` |
| `GET /raw/r/{sub}/comments/{post}/_/{comment}.json` | checking posted replies (`check_replies`) | `PostComments` with the focused comment (already used by the comments route) | route the raw path through the thread code |
| `GET /raw/api/similar_subreddits.json` | "similar communities" in weekly brand maintenance | none known | stays on the JSON route; if refused, the scanner already logs a warning and skips it (no outage) |
| `GET /user/...`, `/best` | not used by DemandLark | not ported | keep the JSON route |

## Design

1. **New pure module `reddit_gql_feed.py`** (no I/O, unit-tested like `reddit_gql.py`): operation names and hashes,
   variable builders, cursor encode/decode, and converters from GraphQL nodes to JSON API things. The post converter
   fills every field the scanner's schema reads (`server/src/redapi/schema.ts`): `id`, `name`, `subreddit`, `title`,
   `selftext`, `author`, `created_utc`, `score`, `num_comments`, `upvote_ratio`, `locked`, `archived`, `over_18`,
   `stickied`, `removed_by_category`, `distinguished`, `is_self`, `permalink`, `url`, `link_flair_text`,
   `crosspost_parent`.
2. **Client** (`reddit_client.py`): one `_gql(operation, variables)` used by every read (generalise today's
   `_graphql`, which is comments-specific): same token, app headers and browser TLS; a 401/403/429 replaces the token
   once; `x-ratelimit-remaining` below 5 drops the token (about 600 queries per token); answers cached like today.
3. **Order of tiers per route**: GraphQL first; the old JSON chain only as a fallback (it may come back, and it costs
   one refused request). Error messages keep the per-tier trail, e.g. `gql:403 … | oauth(loid):403 …`.
4. **Paging**: a `limit=100` read is filled with as many GraphQL pages as needed (page size measured first: try 100,
   fall back to 25), stopping early when the cursor ends. At most 4 queries per listing.
5. **Self-heal and deep health** count GraphQL refusals of listing/search reads (today they count JSON refusals, which
   are now always refused, so the instance would restart for nothing). `GET /health?deep=1` probes with one
   `SubredditFeedElements` read.
6. **Own code**: polak14/redlib (AGPL-3.0) and Amqx/reddit-gql (GPL-3.0) show which operations, hashes and variables the
   app uses. Those facts are used; their code is not copied (our fork has no licence and is a different language).

## Steps

1. Record real GraphQL answers for each operation into `tests/fixtures/` (one small read each, from a developer machine).
2. Build `reddit_gql_feed.py` and its tests against the fixtures (field mapping, cursors, unavailable communities,
   rules, empty pages).
3. Wire the routes in `reddit_client.py` / `main.py`; update self-heal and deep health.
4. **Compare with stored data**: for ~50 posts DemandLark already stored (`reddit_posts`), read them through the new
   path and check title, author, created time, subreddit, flags and permalink match. Score/comments may have grown.
5. Run locally: every route DemandLark uses, plus the scanner's own client tests pointed at the local RedApi.
6. Push to `nastr-o/RedApi`, deploy with the Render API (auto-deploy doesn't fire), check `/health?deep=1`.
7. Live proof: press Refresh as the owner's DemandLark workspace, follow the run (communities read, search done, posts
   stored), open a thread page, then watch the next scheduled run.
8. Demandlark side: move the submodule pointer, update `STATUS.md`, the architecture doc and memory; commit.

## Risks

- **Reddit can retire a query hash** with an app update: that read fails until new hashes are found (Amqx/reddit-gql
  and polak14/redlib track them). Each failure names the operation, so it is quick to see.
- **GraphQL may get a login wall too.** Then nothing logged-out works anywhere, and the choice is the official API
  (`workers/reddit/`, ready) earlier than planned. The owner decides that.
- **Volume**: a scheduled run is about 15–30 reads; with paging, up to ~100 GraphQL queries, well under one token's
  budget. Same Render hours as now.
- **Terms of service**: same position as today's RedApi (development only, until the official API at launch).
