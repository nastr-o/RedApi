"""
Configuration for the Reddit API proxy bot.

This mirrors the approach Redlib (https://github.com/redlib-org/redlib) documents
in its own README: it spoofs the OAuth "installed_client" flow used by the
official Reddit Android app to obtain anonymous, account-less bearer tokens,
then talks to oauth.reddit.com using headers shaped like the real app. When
that path is unavailable, it falls back to Reddit's public, unauthenticated
*.json endpoints on www.reddit.com and old.reddit.com.

IMPORTANT — read before deploying:
  * The client_id below is the historical PUBLIC identifier used by the
    official Reddit Android app for logged-out/"installed client" sessions
    (the same one Redlib's maintainer documented sniffing via mitmproxy).
    It is not a secret and needs no login. Reddit periodically rotates or
    blocks these. If token requests start failing with 400/401, you'll need
    to capture a fresh one yourself (mitmproxy against the real app) or pull
    the current value from Redlib's own src/client.rs, and drop it into
    ANDROID_CLIENT_IDS below.
  * This is an "approximate" reimplementation as requested — it copies
    Redlib's strategy (spoofed installed-client OAuth + 24h refresh +
    header mimicking + domain fallback), not its exact byte-for-byte
    request/response handling.
  * Running high-volume automated scraping against Reddit may run against
    Reddit's Terms of Service / API terms depending on how you use it —
    that's on you to review for your use case.
"""

import random
import uuid

# --- OAuth "installed client" identities ------------------------------------
# device_id is regenerated once per process (random UUID4) -- matches what the
# real app does for anonymous/logged-out sessions (one device id per install).

ANDROID_CLIENT_IDS = [
    "ohXpoqrZYub1kg",  # historical public client_id of the official Android app
    # Add more here (e.g. from other open-source Android Reddit clients) to
    # give the rotation logic somewhere to go if one gets blocked.
]

ANDROID_APP_VERSIONS = [
    "Version 2024.22.1/Build 1652272",
    "Version 2024.23.1/Build 1665606",
    "Version 2024.24.1/Build 1682520",
    "Version 2024.25.0/Build 1693595",
    "Version 2024.25.2/Build 1700401",
    "Version 2024.25.3/Build 1703490",
    "Version 2024.26.0/Build 1710470",
    "Version 2024.26.1/Build 1717435",
    "Version 2024.28.0/Build 1737665",
    "Version 2024.28.1/Build 1741165",
    "Version 2024.30.0/Build 1770787",
    "Version 2024.31.0/Build 1786202",
    "Version 2024.32.0/Build 1809095",
    "Version 2024.32.1/Build 1813258",
    "Version 2024.33.0/Build 1819908",
    "Version 2024.34.0/Build 1837909",
    "Version 2024.35.0/Build 1861437",
    "Version 2024.36.0/Build 1875012",
    "Version 2024.37.0/Build 1888053",
    "Version 2024.38.0/Build 1902791",
    "Version 2024.39.0/Build 1916713",
    "Version 2024.40.0/Build 1928580",
    "Version 2024.41.0/Build 1941199",
    "Version 2024.41.1/Build 1947805",
    "Version 2024.42.0/Build 1952440",
    "Version 2024.43.0/Build 1972250",
    "Version 2024.44.0/Build 1988458",
    "Version 2024.45.0/Build 2001943",
    "Version 2024.46.0/Build 2012731",
    "Version 2024.47.0/Build 2029755",
    "Version 2023.48.0/Build 1319123",
    "Version 2023.49.0/Build 1321715",
    "Version 2023.49.1/Build 1322281",
    "Version 2023.50.0/Build 1332338",
    "Version 2023.50.1/Build 1345844",
    "Version 2024.02.0/Build 1368985",
    "Version 2024.03.0/Build 1379408",
    "Version 2024.04.0/Build 1391236",
    "Version 2024.05.0/Build 1403584",
    "Version 2024.06.0/Build 1418489",
    "Version 2024.07.0/Build 1429651",
    "Version 2024.08.0/Build 1439531",
    "Version 2024.10.0/Build 1470045",
    "Version 2024.10.1/Build 1478645",
    "Version 2024.11.0/Build 1480707",
    "Version 2024.12.0/Build 1494694",
    "Version 2024.13.0/Build 1505187",
    "Version 2024.14.0/Build 1520556",
    "Version 2024.15.0/Build 1536823",
    "Version 2024.16.0/Build 1551366",
    "Version 2024.17.0/Build 1568106",
    "Version 2024.18.0/Build 1577901",
    "Version 2024.18.1/Build 1585304",
    "Version 2024.19.0/Build 1593346",
    "Version 2024.20.0/Build 1612800",
    "Version 2024.20.1/Build 1615586",
    "Version 2024.20.2/Build 1624969",
    "Version 2024.20.3/Build 1624970",
    "Version 2024.21.0/Build 1631686",
    "Version 2024.22.0/Build 1645257",
    "Version 2023.21.0/Build 956283",
    "Version 2023.22.0/Build 968223",
    "Version 2023.23.0/Build 983896",
    "Version 2023.24.0/Build 998541",
    "Version 2023.25.0/Build 1014750",
    "Version 2023.25.1/Build 1018737",
    "Version 2023.26.0/Build 1019073",
    "Version 2023.27.0/Build 1031923",
    "Version 2023.28.0/Build 1046887",
    "Version 2023.29.0/Build 1059855",
    "Version 2023.30.0/Build 1078734",
    "Version 2023.31.0/Build 1091027",
    "Version 2023.32.0/Build 1109919",
    "Version 2023.32.1/Build 1114141",
    "Version 2023.33.1/Build 1129741",
    "Version 2023.34.0/Build 1144243",
    "Version 2023.35.0/Build 1157967",
    "Version 2023.36.0/Build 1168982",
    "Version 2023.37.0/Build 1182743",
    "Version 2023.38.0/Build 1198522",
    "Version 2023.39.0/Build 1211607",
    "Version 2023.39.1/Build 1221505",
    "Version 2023.40.0/Build 1221521",
    "Version 2023.41.0/Build 1233125",
    "Version 2023.41.1/Build 1239615",
    "Version 2023.42.0/Build 1245088",
    "Version 2023.43.0/Build 1257426",
    "Version 2023.44.0/Build 1268622",
    "Version 2023.45.0/Build 1281371",
    "Version 2023.47.0/Build 1303604",
    "Version 2022.42.0/Build 638508",
    "Version 2022.43.0/Build 648277",
    "Version 2022.44.0/Build 664348",
    "Version 2022.45.0/Build 677985",
    "Version 2023.01.0/Build 709875",
    "Version 2023.02.0/Build 717912",
    "Version 2023.03.0/Build 729220",
    "Version 2023.04.0/Build 744681",
    "Version 2023.05.0/Build 755453",
    "Version 2023.06.0/Build 775017",
    "Version 2023.07.0/Build 788827",
    "Version 2023.07.1/Build 790267",
    "Version 2023.08.0/Build 798718",
    "Version 2023.09.0/Build 812015",
    "Version 2023.09.1/Build 816833",
    "Version 2023.10.0/Build 821148",
    "Version 2023.11.0/Build 830610",
    "Version 2023.12.0/Build 841150",
    "Version 2023.13.0/Build 852246",
    "Version 2023.14.0/Build 861593",
    "Version 2023.14.1/Build 864826",
    "Version 2023.15.0/Build 870628",
    "Version 2023.16.0/Build 883294",
    "Version 2023.16.1/Build 886269",
    "Version 2023.17.0/Build 896030",
    "Version 2023.17.1/Build 900542",
    "Version 2023.18.0/Build 911877",
    "Version 2023.19.0/Build 927681",
    "Version 2023.20.0/Build 943980",
    "Version 2023.20.1/Build 946732",
    "Version 2022.20.0/Build 487703",
    "Version 2022.21.0/Build 492436",
    "Version 2022.22.0/Build 498700",
    "Version 2022.23.0/Build 502374",
    "Version 2022.23.1/Build 506606",
    "Version 2022.24.0/Build 510950",
    "Version 2022.24.1/Build 513462",
    "Version 2022.25.0/Build 515072",
    "Version 2022.25.1/Build 516394",
    "Version 2022.25.2/Build 519915",
    "Version 2022.26.0/Build 521193",
    "Version 2022.27.0/Build 527406",
    "Version 2022.27.1/Build 529687",
    "Version 2022.28.0/Build 533235",
    "Version 2022.30.0/Build 548620",
    "Version 2022.31.0/Build 556666",
    "Version 2022.31.1/Build 562612",
    "Version 2022.32.0/Build 567875",
    "Version 2022.33.0/Build 572600",
    "Version 2022.34.0/Build 579352",
    "Version 2022.35.0/Build 588016",
    "Version 2022.35.1/Build 589034",
    "Version 2022.36.0/Build 593102",
    "Version 2022.37.0/Build 601691",
    "Version 2022.38.0/Build 607460",
    "Version 2022.39.0/Build 615385",
    "Version 2022.39.1/Build 619019",
    "Version 2022.40.0/Build 624782",
    "Version 2022.41.0/Build 630468",
    "Version 2022.41.1/Build 634168",
]

ANDROID_OS_STRINGS = ["Android 12", "Android 13", "Android 14"]

ANDROID_DEVICES = [
    "samsung SM-G991B",
    "samsung SM-A536B",
    "Google Pixel 7",
    "OnePlus CPH2449",
]


def random_android_user_agent() -> str:
    """Build a User-Agent shaped like the real Reddit app's:
    Reddit/<version>/<android ver> (<device>)
    """
    version = random.choice(ANDROID_APP_VERSIONS)
    os_ver = random.choice(ANDROID_OS_STRINGS)
    device = random.choice(ANDROID_DEVICES)
    return f"Reddit/{version}/{os_ver} ({device})"


# --- Fallback (unauthenticated) desktop UAs ---------------------------------
DESKTOP_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# --- Endpoint tiers ----------------------------------------------------------
# Order = fallback priority. The client walks down this list whenever a tier
# fails outright (transport error / persistent 403 / exhausted retries).
OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
# The Android app's current logged-out token endpoint (what Redlib's MobileSpoofAuth uses, src/oauth.rs).
LOID_TOKEN_URL = "https://www.reddit.com/auth/v2/oauth/access-token/loid"

# TLS fingerprints to present (curl_cffi impersonation targets). Reddit's edge blocks non-browser handshakes; Redlib
# emulates Chrome/Firefox for the same reason (src/client.rs).
TLS_IMPERSONATE = ["chrome", "firefox"]

FALLBACK_TIERS = [
    {"name": "oauth", "base": "https://oauth.reddit.com", "auth": True},
    {"name": "www-json", "base": "https://www.reddit.com", "auth": False},
    {"name": "old-json", "base": "https://old.reddit.com", "auth": False},
]

TOKEN_LIFETIME_SECONDS = 24 * 60 * 60  # Redlib refreshes every 24h
TOKEN_SAFETY_MARGIN = 60 * 5  # refresh 5 min before actual expiry

MAX_RETRIES_PER_TIER = 3
BACKOFF_BASE_SECONDS = 0.75
REQUEST_TIMEOUT = 10.0

CACHE_TTL_SECONDS = 10  # short-lived cache to absorb bursts of identical calls
CACHE_STALE_MAX_SECONDS = 600  # serve-stale-on-total-failure window


def android_app_headers() -> dict:
    """Headers the Android app sends, as Redlib builds them (src/oauth.rs, Device::android): one device id per
    process, reused for the token request and every read."""
    device_id = str(uuid.uuid4())
    return {
        "User-Agent": random_android_user_agent(),
        "x-reddit-retry": "algo=no-retries",
        "x-reddit-compression": "1",
        "x-reddit-qos": f"{random.uniform(1, 100):.3f}",
        "x-reddit-media-codecs": "available-codecs=video/avc, video/hevc",
        "Content-Type": "application/json; charset=UTF-8",
        "client-vendor-id": device_id,
        "X-Reddit-Device-Id": device_id,
    }
