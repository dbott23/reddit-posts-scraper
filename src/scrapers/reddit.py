"""Reddit scraper using Reddit's public JSON API — no credentials required."""

from __future__ import annotations

import asyncio
import datetime
import re
from typing import Any

import httpx

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RedditPostsScraper/1.0)",
    "Accept": "application/json",
}
_DELAY = 1.0  # seconds between paginated requests


def _ts(val: float | None) -> str | None:
    if not val:
        return None
    try:
        return datetime.datetime.fromtimestamp(val, tz=datetime.timezone.utc).isoformat()
    except Exception:
        return None


def _safe_int(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _client(proxy_url: str | None = None) -> httpx.AsyncClient:
    proxies = {"http://": proxy_url, "https://": proxy_url} if proxy_url else None
    return httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True, proxies=proxies)


def _parse_post(data: dict[str, Any]) -> dict[str, Any]:
    permalink = data.get("permalink") or ""
    thumbnail = data.get("thumbnail")
    if thumbnail in (None, "self", "default", "nsfw", "spoiler", ""):
        thumbnail = None
    return {
        "id": data.get("id"),
        "url": f"https://www.reddit.com{permalink}" if permalink.startswith("/") else (data.get("url") or ""),
        "title": data.get("title") or "",
        "text": data.get("selftext") or None,
        "author": data.get("author") or None,
        "subreddit": data.get("subreddit") or None,
        "score": _safe_int(data.get("score")),
        "upvoteRatio": data.get("upvote_ratio"),
        "numComments": _safe_int(data.get("num_comments")),
        "createdAt": _ts(data.get("created_utc")),
        "isVideo": bool(data.get("is_video")),
        "isSelf": bool(data.get("is_self")),
        "linkUrl": data.get("url") if not data.get("is_self") else None,
        "thumbnail": thumbnail,
        "flair": data.get("link_flair_text") or None,
        "awards": _safe_int(data.get("total_awards_received")),
    }


def _parse_comment(data: dict[str, Any], post_url: str | None) -> dict[str, Any] | None:
    author = data.get("author") or ""
    if not author or author in ("[deleted]", "AutoModerator"):
        return None
    body = data.get("body") or ""
    if body in ("[deleted]", "[removed]", ""):
        return None
    return {
        "id": data.get("id"),
        "url": post_url,
        "text": body,
        "author": author,
        "subreddit": data.get("subreddit") or None,
        "score": _safe_int(data.get("score")),
        "createdAt": _ts(data.get("created_utc")),
        "isTopLevel": _safe_int(data.get("depth")) == 0,
        "postId": (data.get("link_id") or "").replace("t3_", "") or None,
    }


def _flatten_comments(
    listing: dict, post_url: str | None, results: list, seen: set, max_results: int
) -> None:
    children = (listing.get("data") or {}).get("children") or []
    for child in children:
        if len(results) >= max_results:
            break
        kind = child.get("kind")
        data = child.get("data") or {}
        if kind == "t1":
            parsed = _parse_comment(data, post_url)
            if parsed:
                uid = parsed.get("id") or ""
                if uid not in seen:
                    seen.add(uid)
                    results.append(parsed)
            replies = data.get("replies")
            if replies and isinstance(replies, dict) and len(results) < max_results:
                _flatten_comments(replies, post_url, results, seen, max_results)


async def _get_listing(
    client: httpx.AsyncClient, url: str, params: dict, max_results: int
) -> list[dict[str, Any]]:
    """Paginate a Reddit listing endpoint, yielding parsed post dicts."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    after: str | None = None

    while len(results) < max_results:
        p = {**params, "limit": min(100, max_results - len(results))}
        if after:
            p["after"] = after
        resp = await client.get(url, params=p)
        resp.raise_for_status()
        body = resp.json()
        listing_data = body.get("data") or {}
        children = listing_data.get("children") or []
        after = listing_data.get("after")
        if not children:
            break
        for child in children:
            if len(results) >= max_results:
                break
            if child.get("kind") == "t3":
                parsed = _parse_post(child.get("data") or {})
                uid = parsed.get("id") or parsed.get("url") or ""
                if uid not in seen:
                    seen.add(uid)
                    results.append(parsed)
        if not after:
            break
        await asyncio.sleep(_DELAY)

    return results[:max_results]


async def scrape_search(
    proxy_url: str | None,
    query: str,
    max_results: int,
    sort: str = "relevance",
    time_filter: str = "all",
    subreddit: str | None = None,
) -> list[dict[str, Any]]:
    base = f"https://www.reddit.com/r/{subreddit}/search.json" if subreddit else "https://www.reddit.com/search.json"
    params: dict = {"q": query, "sort": sort, "t": time_filter, "type": "link"}
    if subreddit:
        params["restrict_sr"] = "1"

    async with _client(proxy_url) as client:
        results = await _get_listing(client, base, params, max_results)

    return [{**p, "source": "search", "query": query} for p in results]


async def scrape_subreddit(
    proxy_url: str | None,
    subreddit: str,
    max_results: int,
    sort: str = "hot",
    time_filter: str = "all",
) -> list[dict[str, Any]]:
    subreddit = subreddit.lstrip("r/")
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    params: dict = {}
    if sort in ("top", "controversial") and time_filter != "all":
        params["t"] = time_filter

    async with _client(proxy_url) as client:
        results = await _get_listing(client, url, params, max_results)

    return [{**p, "source": "subreddit"} for p in results]


async def scrape_post_comments(
    proxy_url: str | None,
    post_url: str,
    max_results: int,
    sort: str = "top",
) -> list[dict[str, Any]]:
    m = re.search(r"/comments/([a-zA-Z0-9]+)", post_url)
    if not m:
        return []
    post_id = m.group(1)
    url = f"https://www.reddit.com/comments/{post_id}.json"

    async with _client(proxy_url) as client:
        resp = await client.get(url, params={"sort": sort, "limit": min(500, max_results * 3)})
        resp.raise_for_status()
        data = resp.json()

    if not isinstance(data, list) or len(data) < 2:
        return []

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    _flatten_comments(data[1], post_url, results, seen, max_results)
    return [{**c, "source": "comment"} for c in results[:max_results]]


async def scrape_user(
    proxy_url: str | None,
    username: str,
    max_results: int,
    sort: str = "new",
) -> list[dict[str, Any]]:
    username = username.lstrip("u/")
    url = f"https://www.reddit.com/user/{username}/submitted.json"
    params: dict = {"sort": sort}

    async with _client(proxy_url) as client:
        results = await _get_listing(client, url, params, max_results)

    return [{**p, "source": "user"} for p in results]
