"""Reddit scraper.

Primary path: Arctic Shift public API (no credentials required, works from cloud IPs).
Authenticated path: Reddit OAuth JSON API (unlocks global keyword search).
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import re
from typing import Any

import httpx

_ARCTIC = "https://arctic-shift.photon-reddit.com/api"
_OAUTH_BASE = "https://oauth.reddit.com"
_HEADERS = {"User-Agent": "Mozilla/5.0 ApifyBot/1.0"}


def _ts_utc(epoch: float | int | None) -> str | None:
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).isoformat()


def _safe_int(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# OAuth helpers (used when credentials are provided)
# ---------------------------------------------------------------------------

async def fetch_oauth_token(
    client_id: str,
    client_secret: str,
    username: str,
    password: str,
) -> str:
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            headers={
                "Authorization": f"Basic {creds}",
                "User-Agent": f"python:apify-reddit-scraper:1.0 (by /u/{username})",
            },
            data={"grant_type": "password", "username": username, "password": password},
        )
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"OAuth failed: {data}")
        return data["access_token"]


def _oauth_client(token: str, username: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_OAUTH_BASE,
        headers={
            "Authorization": f"bearer {token}",
            "User-Agent": f"python:apify-reddit-scraper:1.0 (by /u/{username})",
            "Accept": "application/json",
        },
        timeout=30,
        follow_redirects=True,
    )


def _arctic_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_ARCTIC,
        headers=_HEADERS,
        timeout=30,
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Arctic Shift parsers
# ---------------------------------------------------------------------------

def _parse_arctic_post(p: dict[str, Any], source: str) -> dict[str, Any] | None:
    if not p.get("title") or not p.get("id"):
        return None
    permalink = p.get("permalink") or f"/r/{p.get('subreddit','')}/comments/{p['id']}/"
    url = f"https://www.reddit.com{permalink}"
    thumbnail = p.get("thumbnail") or None
    if thumbnail in ("self", "default", "nsfw", "spoiler", "image", ""):
        thumbnail = None
    if thumbnail and not thumbnail.startswith("http"):
        thumbnail = None
    return {
        "id": p.get("id"),
        "url": url,
        "title": p.get("title", ""),
        "text": p.get("selftext") or None,
        "author": p.get("author") or None,
        "subreddit": p.get("subreddit") or None,
        "score": _safe_int(p.get("score", 0)),
        "upvoteRatio": p.get("upvote_ratio"),
        "numComments": _safe_int(p.get("num_comments", 0)),
        "createdAt": _ts_utc(p.get("created_utc")),
        "isVideo": bool(p.get("is_video")),
        "isSelf": bool(p.get("is_self")),
        "linkUrl": p.get("url") if not p.get("is_self") else None,
        "thumbnail": thumbnail,
        "flair": p.get("link_flair_text") or None,
        "awards": _safe_int(p.get("total_awards_received", 0)),
        "source": source,
    }


def _parse_arctic_comment(c: dict[str, Any], post_url: str, post_id: str) -> dict[str, Any] | None:
    body = (c.get("body") or "").strip()
    if not body or body in ("[deleted]", "[removed]"):
        return None
    author = c.get("author") or None
    if author in ("[deleted]", None):
        return None
    permalink = c.get("permalink") or ""
    return {
        "id": c.get("id"),
        "url": f"https://www.reddit.com{permalink}" if permalink else post_url,
        "text": body,
        "author": author,
        "subreddit": c.get("subreddit") or None,
        "score": _safe_int(c.get("score", 0)),
        "createdAt": _ts_utc(c.get("created_utc")),
        "isTopLevel": (c.get("parent_id") or "").startswith("t3_"),
        "postId": post_id,
        "source": "comment",
    }


# ---------------------------------------------------------------------------
# OAuth JSON API parsers (Reddit's own format)
# ---------------------------------------------------------------------------

def _parse_reddit_post(child: dict[str, Any], source: str) -> dict[str, Any] | None:
    d = child.get("data", {})
    if not d.get("title") or not d.get("permalink"):
        return None
    url = f"https://www.reddit.com{d['permalink']}"
    thumbnail = d.get("thumbnail") or None
    if thumbnail in ("self", "default", "nsfw", "spoiler", "image", ""):
        thumbnail = None
    if thumbnail and not thumbnail.startswith("http"):
        thumbnail = None
    preview_images = d.get("preview", {}).get("images", [])
    if preview_images and not thumbnail:
        src = preview_images[0].get("source", {}).get("url", "")
        if src:
            thumbnail = src.replace("&amp;", "&")
    return {
        "id": d.get("id"),
        "url": url,
        "title": d.get("title", ""),
        "text": d.get("selftext") or None,
        "author": d.get("author") or None,
        "subreddit": d.get("subreddit") or None,
        "score": _safe_int(d.get("score", 0)),
        "upvoteRatio": d.get("upvote_ratio"),
        "numComments": _safe_int(d.get("num_comments", 0)),
        "createdAt": _ts_utc(d.get("created_utc")),
        "isVideo": bool(d.get("is_video")),
        "isSelf": bool(d.get("is_self")),
        "linkUrl": d.get("url") if not d.get("is_self") else None,
        "thumbnail": thumbnail,
        "flair": d.get("link_flair_text") or None,
        "awards": _safe_int(d.get("total_awards_received", 0)),
        "source": source,
    }


def _parse_reddit_comment(child: dict[str, Any], post_url: str) -> dict[str, Any] | None:
    d = child.get("data", {})
    body = (d.get("body") or "").strip()
    if not body or body in ("[deleted]", "[removed]"):
        return None
    author = d.get("author") or None
    if author in ("[deleted]", None):
        return None
    sr_m = re.search(r"/r/([^/]+)/", post_url)
    return {
        "id": d.get("id"),
        "url": f"https://www.reddit.com{d.get('permalink', '')}",
        "text": body,
        "author": author,
        "subreddit": d.get("subreddit") or (sr_m.group(1) if sr_m else None),
        "score": _safe_int(d.get("score", 0)),
        "createdAt": _ts_utc(d.get("created_utc")),
        "isTopLevel": d.get("parent_id", "").startswith("t3_"),
        "postId": d.get("link_id", "").removeprefix("t3_") or None,
        "source": "comment",
    }


# ---------------------------------------------------------------------------
# Public scrape functions
# ---------------------------------------------------------------------------

async def scrape_search(
    proxy_url: str | None,
    query: str,
    max_results: int,
    sort: str = "relevance",
    time_filter: str = "all",
    subreddit: str | None = None,
    oauth_token: str | None = None,
    oauth_username: str = "",
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    if oauth_token:
        # Reddit OAuth: supports global keyword search
        async with _oauth_client(oauth_token, oauth_username) as client:
            params: dict[str, Any] = {
                "q": query, "sort": sort, "t": time_filter,
                "type": "link", "limit": min(100, max_results),
            }
            path = f"/r/{subreddit}/search" if subreddit else "/search"
            if subreddit:
                params["restrict_sr"] = "1"
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            for child in resp.json().get("data", {}).get("children", []):
                if len(results) >= max_results:
                    break
                parsed = _parse_reddit_post(child, "search")
                if parsed:
                    uid = parsed["id"] or parsed["url"]
                    if uid not in seen:
                        seen.add(uid)
                        results.append({**parsed, "query": query})
    else:
        # Arctic Shift: requires a subreddit scope
        if not subreddit:
            raise RuntimeError(
                "Global keyword search requires Reddit API credentials. "
                "Please provide redditClientId, redditClientSecret, redditUsername, and redditPassword, "
                "or add a 'subredditFilter' to restrict the search to a specific subreddit."
            )
        async with _arctic_client() as client:
            params = {
                "query": query, "subreddit": subreddit,
                "limit": min(100, max_results), "sort": "score",
            }
            resp = await client.get("/posts/search", params=params)
            resp.raise_for_status()
            for p in resp.json().get("data", []) or []:
                if len(results) >= max_results:
                    break
                parsed = _parse_arctic_post(p, "search")
                if parsed:
                    uid = parsed["id"] or parsed["url"]
                    if uid not in seen:
                        seen.add(uid)
                        results.append({**parsed, "query": query})

    return results[:max_results]


async def scrape_subreddit(
    proxy_url: str | None,
    subreddit: str,
    max_results: int,
    sort: str = "hot",
    time_filter: str = "all",
    oauth_token: str | None = None,
    oauth_username: str = "",
) -> list[dict[str, Any]]:
    subreddit = subreddit.lstrip("r/")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    if oauth_token:
        async with _oauth_client(oauth_token, oauth_username) as client:
            params: dict[str, Any] = {"limit": min(100, max_results)}
            if sort in ("top", "controversial") and time_filter != "all":
                params["t"] = time_filter
            resp = await client.get(f"/r/{subreddit}/{sort}", params=params)
            resp.raise_for_status()
            for child in resp.json().get("data", {}).get("children", []):
                if len(results) >= max_results:
                    break
                parsed = _parse_reddit_post(child, "subreddit")
                if parsed:
                    uid = parsed["id"] or parsed["url"]
                    if uid not in seen:
                        seen.add(uid)
                        results.append(parsed)
    else:
        # Arctic Shift: sort by score descending (closest to "hot/top")
        async with _arctic_client() as client:
            params = {
                "subreddit": subreddit,
                "limit": min(100, max_results),
                "sort": "score",
                "order": "desc",
            }
            resp = await client.get("/posts/search", params=params)
            resp.raise_for_status()
            for p in resp.json().get("data", []) or []:
                if len(results) >= max_results:
                    break
                parsed = _parse_arctic_post(p, "subreddit")
                if parsed:
                    uid = parsed["id"] or parsed["url"]
                    if uid not in seen:
                        seen.add(uid)
                        results.append(parsed)

    return results[:max_results]


async def scrape_post_comments(
    proxy_url: str | None,
    post_url: str,
    max_results: int,
    sort: str = "top",
    oauth_token: str | None = None,
    oauth_username: str = "",
) -> list[dict[str, Any]]:
    m = re.search(r"/r/([^/]+)/comments/([a-zA-Z0-9]+)", post_url)
    if not m:
        return []
    subreddit, post_id = m.group(1), m.group(2)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    if oauth_token:
        async with _oauth_client(oauth_token, oauth_username) as client:
            resp = await client.get(
                f"/r/{subreddit}/comments/{post_id}",
                params={"sort": sort, "limit": min(100, max_results), "depth": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            if len(data) < 2:
                return []
            for child in data[1].get("data", {}).get("children", []):
                if child.get("kind") != "t1":
                    continue
                if len(results) >= max_results:
                    break
                parsed = _parse_reddit_comment(child, post_url)
                if parsed:
                    uid = parsed["id"] or parsed["url"]
                    if uid not in seen:
                        seen.add(uid)
                        results.append(parsed)
    else:
        async with _arctic_client() as client:
            resp = await client.get(
                "/comments/search",
                params={"link_id": post_id, "limit": min(100, max_results)},
            )
            resp.raise_for_status()
            for c in resp.json().get("data", []) or []:
                if len(results) >= max_results:
                    break
                parsed = _parse_arctic_comment(c, post_url, post_id)
                if parsed:
                    uid = parsed["id"] or parsed["url"]
                    if uid not in seen:
                        seen.add(uid)
                        results.append(parsed)

    return results[:max_results]


async def scrape_user(
    proxy_url: str | None,
    username: str,
    max_results: int,
    sort: str = "new",
    oauth_token: str | None = None,
    oauth_username: str = "",
) -> list[dict[str, Any]]:
    username = username.lstrip("u/")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    if oauth_token:
        async with _oauth_client(oauth_token, oauth_username) as client:
            resp = await client.get(
                f"/user/{username}/submitted",
                params={"sort": sort, "limit": min(100, max_results)},
            )
            resp.raise_for_status()
            for child in resp.json().get("data", {}).get("children", []):
                if len(results) >= max_results:
                    break
                parsed = _parse_reddit_post(child, "user")
                if parsed:
                    uid = parsed["id"] or parsed["url"]
                    if uid not in seen:
                        seen.add(uid)
                        results.append(parsed)
    else:
        async with _arctic_client() as client:
            resp = await client.get(
                "/posts/search",
                params={"author": username, "limit": min(100, max_results), "sort": "created_utc", "order": "desc"},
            )
            resp.raise_for_status()
            for p in resp.json().get("data", []) or []:
                if len(results) >= max_results:
                    break
                parsed = _parse_arctic_post(p, "user")
                if parsed:
                    uid = parsed["id"] or parsed["url"]
                    if uid not in seen:
                        seen.add(uid)
                        results.append(parsed)

    return results[:max_results]
