"""
RedditClient -- an approximate reimplementation of how Redlib talks to Reddit.

Flow, in priority order, matching what redlib-org/redlib documents about
itself (README "Built with" section + the maintainer's public write-up on
fixing Libreddit's rate-limit problems):

1. OAuth "installed_client" token spoofing
   POST to /api/v1/access_token with
     grant_type=https://oauth.reddit.com/grants/installed_client
   plus a random per-process device_id, authenticated with HTTP Basic auth
   using a public Android client_id and a blank secret. This is the same
   anonymous/logged-out flow the real Reddit Android app uses, so Reddit
   treats it like real app traffic (and applies the app's much higher rate
   limit) instead of throttling it like a generic script.

2. Authenticated requests to oauth.reddit.com
   Every call carries `Authorization: Bearer <token>` plus an Android-shaped
   User-Agent, mimicking the real app's headers.

3. Token refresh
   Cached in memory and refreshed ~24h, matching the official app's cadence,
   instead of re-authenticating on every request.

4. Fallbacks, attempted in order whenever a tier is exhausted:
   a. Retry the same tier with exponential backoff on 429 / 5xx
      (soft rate limit or transient server error)
   b. Force-refresh the token once on 401/403 (token expired/revoked early)
   c. Rotate to the next client_id in the pool if the current one keeps
      getting rejected at the token endpoint itself
   d. Drop OAuth entirely and hit the public, unauthenticated *.json
      endpoint on www.reddit.com (no token, no app-tier rate limit boost,
      but still works for public subreddits/posts/users)
   e. If www.reddit.com is also failing, try old.reddit.com/*.json
   f. If every live tier fails, serve the last good cached response for
      that exact request if it isn't too stale, rather than erroring out
"""

import asyncio
import json
import re
import random
import time
import uuid
from typing import Any, Optional

from curl_cffi import requests as cffi
from curl_cffi.requests.exceptions import RequestException

import config
from cache import TTLCache


def comments_transport_path(path: str) -> str:
    """Encode one separator in a comments route, as Redlib does (src/client.rs, comments_transport_path): Reddit blocks
    literal anonymous comments routes, so "/r/sub/comments/id" goes out as "/r/sub%2Fcomments/id" and "/comments/id" as
    "/comments%2Fid". Every other path is unchanged."""
    if path.startswith("/comments/"):
        separator = len("/comments")
    elif path.startswith("/r/"):
        subreddit, _, rest = path[3:].partition("/")
        if not subreddit or not rest.startswith("comments/"):
            return path
        separator = 3 + len(subreddit)
    else:
        return path
    post_id = path[separator + 1:].split("/")[0] if path.startswith("/comments/") else path[separator + 1:].split("/")[1]
    if not post_id or post_id == ".json":
        return path
    return f"{path[:separator]}%2F{path[separator + 1:]}"


class RedditAPIError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class _Token:
    __slots__ = ("value", "expires_at", "client_id", "flow")

    def __init__(self, value: str, expires_in: int, client_id: str, flow: str = "installed_client"):
        self.value = value
        self.flow = flow
        self.expires_at = time.monotonic() + expires_in
        self.client_id = client_id

    @property
    def is_fresh(self) -> bool:
        return time.monotonic() < (self.expires_at - config.TOKEN_SAFETY_MARGIN)


class RedditClient:
    def __init__(self) -> None:
        # Reddit's edge ("blocked by network security") rejects clients by their TLS fingerprint, so requests go out with
        # a real browser's handshake, as Redlib does with wreq's emulation profiles.
        self._http = cffi.AsyncSession(
            impersonate=random.choice(config.TLS_IMPERSONATE),
            timeout=config.REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        self._token: Optional[_Token] = None
        # Session headers Reddit returns with a loid token; sent back on every authenticated read.
        self._session_headers: dict[str, str] = {}
        self._app_headers = config.android_app_headers()
        self._token_lock = asyncio.Lock()
        self._device_id = str(uuid.uuid4())
        self._client_id_index = 0
        self._cache = TTLCache()

    async def aclose(self) -> None:
        await self._http.close()

    # ---------------------------------------------------------------- token

    def _current_client_id(self) -> str:
        return config.ANDROID_CLIENT_IDS[self._client_id_index % len(config.ANDROID_CLIENT_IDS)]

    def _rotate_client_id(self) -> None:
        self._client_id_index += 1

    async def _fetch_token(self, client_id: str) -> _Token:
        """Logged-out token the way Redlib gets it today: the Android app's loid endpoint first, the older
        installed_client grant as a fallback."""
        try:
            return await self._fetch_loid_token(client_id)
        except (RedditAPIError, RequestException, ValueError, KeyError):
            return await self._fetch_installed_client_token(client_id)

    async def _fetch_loid_token(self, client_id: str) -> _Token:
        resp = await self._http.post(
            config.LOID_TOKEN_URL,
            headers=self._app_headers,
            auth=(client_id, ""),
            data=json.dumps({"scopes": ["*", "email", "pii"]}),
        )
        if resp.status_code != 200:
            raise RedditAPIError(f"loid token request failed ({resp.status_code})", status_code=resp.status_code)
        payload = resp.json()
        self._session_headers = {
            name: resp.headers[name] for name in ("x-reddit-loid", "x-reddit-session") if resp.headers.get(name)
        }
        return _Token(
            value=payload["access_token"],
            expires_in=payload.get("expires_in", config.TOKEN_LIFETIME_SECONDS),
            client_id=client_id,
            flow="loid",
        )

    async def _fetch_installed_client_token(self, client_id: str) -> _Token:
        """Anonymous 'installed_client' grant -- the flow the official
        Android app used for logged-out browsing. No username/password."""
        self._session_headers = {}
        headers = {
            "User-Agent": config.random_android_user_agent(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "https://oauth.reddit.com/grants/installed_client",
            "device_id": self._device_id,
        }
        resp = await self._http.post(
            config.OAUTH_TOKEN_URL,
            data=data,
            headers=headers,
            auth=(client_id, ""),  # public client id, blank secret
        )
        if resp.status_code != 200:
            raise RedditAPIError(
                f"token request failed ({resp.status_code}): {resp.text[:200]}",
                status_code=resp.status_code,
            )
        payload = resp.json()
        return _Token(
            value=payload["access_token"],
            expires_in=payload.get("expires_in", config.TOKEN_LIFETIME_SECONDS),
            client_id=client_id,
        )

    async def _get_token(self, force_refresh: bool = False) -> _Token:
        async with self._token_lock:
            if self._token and self._token.is_fresh and not force_refresh:
                return self._token

            last_error: Optional[Exception] = None
            for _ in range(len(config.ANDROID_CLIENT_IDS)):
                client_id = self._current_client_id()
                try:
                    self._token = await self._fetch_token(client_id)
                    return self._token
                except (RedditAPIError, RequestException) as exc:
                    last_error = exc
                    self._rotate_client_id()  # this id looks burnt, try next
            raise RedditAPIError(f"all OAuth client ids failed: {last_error}")

    # ------------------------------------------------------------- requests

    async def _request_oauth(self, path: str, params: dict) -> cffi.Response:
        token = await self._get_token()
        headers = {
            **self._app_headers,
            **self._session_headers,
            "Authorization": f"Bearer {token.value}",
        }
        headers.pop("Content-Type", None)
        url = f"{config.FALLBACK_TIERS[0]['base']}{comments_transport_path(path)}"
        resp = await self._http.get(url, params=params, headers=headers)
        if resp.status_code in (401, 403):
            # token likely expired/revoked early -- refresh once and retry
            token = await self._get_token(force_refresh=True)
            headers.update(self._session_headers)
            headers["Authorization"] = f"Bearer {token.value}"
            resp = await self._http.get(url, params=params, headers=headers)
        return resp

    async def _request_public_json(self, base: str, path: str, params: dict) -> cffi.Response:
        headers = {"User-Agent": random.choice(config.DESKTOP_USER_AGENTS)}
        url = f"{base}{path}"
        return await self._http.get(url, params=params, headers=headers)

    async def _request_tier(self, tier: dict, path: str, params: dict) -> cffi.Response:
        if tier["auth"]:
            return await self._request_oauth(path, params)
        return await self._request_public_json(tier["base"], path, params)

    async def get(self, path: str, params: Optional[dict] = None) -> Any:
        """Fetch a Reddit JSON resource by path, e.g. '/r/python/hot',
        walking the full fallback chain. Returns parsed JSON."""
        params = dict(params or {})
        if not path.endswith(".json"):
            path = f"{path}.json"

        cache_key = f"{path}?{sorted(params.items())}"
        cached = self._cache.get_fresh(cache_key, config.CACHE_TTL_SECONDS)
        if cached is not None:
            return cached

        last_status = 502
        last_detail = "unknown error"
        # One entry per failed attempt, e.g. "oauth(loid):403 blocked by network security", so an outage says which
        # tier failed and how, not just the last fallback's answer.
        trail: list[str] = []

        def note(tier_name: str, resp: Any = None, error: str = "") -> None:
            label = tier_name
            if tier_name == "oauth" and self._token is not None:
                label = f"oauth({self._token.flow})"
            if resp is None:
                trail.append(f"{label}:{error}"[:120])
                return
            snippet = " ".join((resp.text or "")[:200].split())
            if "<" in snippet:  # an HTML block page: keep only its readable words

                snippet = " ".join(re.sub(r"<[^>]*>|\{[^}]*\}", " ", resp.text or "").split())[:80]
            trail.append(f"{label}:{resp.status_code} {snippet[:80]}".strip())

        for tier in config.FALLBACK_TIERS:
            for attempt in range(config.MAX_RETRIES_PER_TIER):
                try:
                    resp = await self._request_tier(tier, path, params)
                except RequestException as exc:
                    last_detail = f"{tier['name']} transport error: {exc}"
                    note(tier["name"], error=f"transport {type(exc).__name__}")
                    await asyncio.sleep(config.BACKOFF_BASE_SECONDS * (2 ** attempt))
                    continue

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except ValueError:
                        last_detail = f"{tier['name']} returned non-JSON body"
                        note(tier["name"], resp)
                        break  # don't retry a parse failure, just try next tier
                    self._cache.set(cache_key, data)
                    return data

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("retry-after", 0)) or (
                        config.BACKOFF_BASE_SECONDS * (2 ** attempt)
                    )
                    last_status, last_detail = 429, f"{tier['name']} rate limited"
                    note(tier["name"], resp)
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status_code in (401, 403) or 300 <= resp.status_code < 400:
                    # 401/403, or a redirect to the login page: this tier is refused; try the next one.
                    last_status = resp.status_code
                    last_detail = f"{tier['name']} returned {resp.status_code}"
                    note(tier["name"], resp)
                    break  # next tier, retrying here won't help

                if 500 <= resp.status_code < 600:
                    last_status = resp.status_code
                    last_detail = f"{tier['name']} server error {resp.status_code}"
                    note(tier["name"], resp)
                    await asyncio.sleep(config.BACKOFF_BASE_SECONDS * (2 ** attempt))
                    continue

                # any other 4xx (404, etc.) is a real answer -- don't mask it
                raise RedditAPIError(
                    f"{tier['name']} returned {resp.status_code}",
                    status_code=resp.status_code,
                )

        # every live tier exhausted -- last resort: stale cache
        stale = self._cache.get_stale(cache_key, config.CACHE_STALE_MAX_SECONDS)
        if stale is not None:
            return stale

        raise RedditAPIError(f"all tiers failed: {' | '.join(trail) or last_detail}", status_code=last_status)


reddit_client = RedditClient()
