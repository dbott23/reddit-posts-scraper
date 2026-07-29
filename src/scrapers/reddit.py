"""Reddit scraper using Playwright (camoufox) — no credentials required."""

from __future__ import annotations

import asyncio
import datetime
import json
import re
from typing import Any

from bs4 import BeautifulSoup
from camoufox.async_api import AsyncCamoufox


def _ts(val: str | None) -> str | None:
    if not val:
        return None
    try:
        return datetime.datetime.fromisoformat(val.replace("Z", "+00:00")).isoformat()
    except Exception:
        return None


def _safe_int(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _parse_post_element(el: Any) -> dict[str, Any] | None:
    """Parse a <shreddit-post> custom element (subreddit/user pages)."""
    permalink = el.get("permalink") or ""
    title = el.get("post-title") or el.get("title") or ""
    if not title or not permalink:
        return None
    return {
        "id": el.get("id") or el.get("post-id") or None,
        "url": f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink,
        "title": title,
        "text": None,
        "author": el.get("author") or None,
        "subreddit": (el.get("subreddit-prefixed-name") or "").lstrip("r/") or el.get("subreddit") or None,
        "score": _safe_int(el.get("score")),
        "upvoteRatio": None,
        "numComments": _safe_int(el.get("comment-count")),
        "createdAt": _ts(el.get("created-timestamp")),
        "isVideo": el.get("content-href", "").endswith((".mp4", ".webm")) if el.get("content-href") else False,
        "isSelf": el.get("post-type") == "self",
        "linkUrl": el.get("content-href") or None,
        "thumbnail": el.get("thumbnail-url") or None,
        "flair": el.get("flair-text") or None,
        "awards": 0,
    }


def _parse_search_card(card: Any) -> dict[str, Any] | None:
    """Parse a search result card div (data-testid='search-post-with-content-preview')."""
    link = card.find("a", attrs={"data-testid": "post-title"})
    if not link:
        return None
    href = link.get("href") or ""
    title = link.get("aria-label") or link.get_text(strip=True)
    if not title or not href:
        return None

    # Extract metadata from JSON tracking context
    tracker = card.find("search-telemetry-tracker", attrs={"click-events": "search/click/post"})
    meta: dict[str, Any] = {}
    if tracker:
        ctx_raw = tracker.get("data-faceplate-tracking-context") or ""
        try:
            ctx = json.loads(ctx_raw.replace("&quot;", '"'))
            meta = ctx
        except Exception:
            pass

    post_id = (meta.get("post") or {}).get("id", "").replace("t3_", "") or None
    author = (meta.get("profile") or {}).get("name") or None
    subreddit = (meta.get("subreddit") or {}).get("name") or None
    snippet = (meta.get("search") or {}).get("snippet") or None

    # Score and comment count from faceplate-number elements
    nums = card.find_all("faceplate-number")
    score = _safe_int(nums[0].get("number")) if len(nums) > 0 else 0
    num_comments = _safe_int(nums[1].get("number")) if len(nums) > 1 else 0

    # Timestamp from faceplate-timeago
    timeago = card.find("faceplate-timeago")
    created_at = _ts(timeago.get("ts")) if timeago else None

    return {
        "id": post_id,
        "url": f"https://www.reddit.com{href}" if href.startswith("/") else href,
        "title": title,
        "text": snippet,
        "author": author,
        "subreddit": subreddit,
        "score": score,
        "upvoteRatio": None,
        "numComments": num_comments,
        "createdAt": created_at,
        "isVideo": False,
        "isSelf": None,
        "linkUrl": None,
        "thumbnail": None,
        "flair": None,
        "awards": 0,
    }


def _parse_comment_element(el: Any, post_url: str | None) -> dict[str, Any] | None:
    """Parse a <shreddit-comment> element."""
    author = el.get("author") or ""
    body = el.get("body-html") or ""
    if not author or author == "[deleted]":
        return None
    body_text = BeautifulSoup(body, "html.parser").get_text(separator=" ").strip() if body else None
    depth = _safe_int(el.get("depth")) or 0
    return {
        "id": el.get("thingid") or el.get("id") or None,
        "url": post_url,
        "text": body_text,
        "author": author,
        "subreddit": el.get("subreddit") or None,
        "score": _safe_int(el.get("score")),
        "createdAt": _ts(el.get("created-timestamp")),
        "isTopLevel": depth == 0,
        "postId": None,
    }


async def _scroll_and_collect_shreddit(
    page: Any, max_results: int
) -> list[dict[str, Any]]:
    """Collect shreddit-post elements (subreddit/user pages) by scrolling."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    stall = 0

    while len(results) < max_results and stall < 4:
        html = await page.inner_html("body")
        soup = BeautifulSoup(html, "html.parser")
        new = 0
        for el in soup.find_all("shreddit-post"):
            if len(results) >= max_results:
                break
            parsed = _parse_post_element(el)
            if not parsed:
                continue
            uid = parsed.get("id") or parsed.get("url") or ""
            if uid in seen:
                continue
            seen.add(uid)
            results.append(parsed)
            new += 1
        stall = 0 if new else stall + 1
        if len(results) < max_results:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

    return results[:max_results]


async def _scroll_and_collect_search(
    page: Any, max_results: int
) -> list[dict[str, Any]]:
    """Collect search result cards by scrolling (DOM approach)."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    stall = 0

    while len(results) < max_results and stall < 5:
        html = await page.inner_html("body")
        soup = BeautifulSoup(html, "html.parser")
        new = 0
        for card in soup.find_all("div", attrs={"data-testid": "search-post-with-content-preview"}):
            if len(results) >= max_results:
                break
            parsed = _parse_search_card(card)
            if not parsed:
                continue
            uid = parsed.get("id") or parsed.get("url") or ""
            if uid in seen:
                continue
            seen.add(uid)
            results.append(parsed)
            new += 1
        stall = 0 if new else stall + 1
        if len(results) < max_results:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

    return results[:max_results]


async def scrape_search(
    proxy_url: str | None,
    query: str,
    max_results: int,
    sort: str = "relevance",
    time_filter: str = "all",
    subreddit: str | None = None,
) -> list[dict[str, Any]]:
    base = f"https://www.reddit.com/r/{subreddit}/search/" if subreddit else "https://www.reddit.com/search/"
    url = f"{base}?q={query}&sort={sort}&t={time_filter}&type=link"

    async with AsyncCamoufox(headless=True, proxy={"server": proxy_url} if proxy_url else None) as browser:
        page = await browser.new_page()
        # Visit homepage first to establish session (required for search to render posts)
        await page.goto("https://www.reddit.com/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        results = await _scroll_and_collect_search(page, max_results)

    return [{**p, "source": "search", "query": query} for p in results]


async def scrape_subreddit(
    proxy_url: str | None,
    subreddit: str,
    max_results: int,
    sort: str = "hot",
    time_filter: str = "all",
) -> list[dict[str, Any]]:
    subreddit = subreddit.lstrip("r/")
    url = f"https://www.reddit.com/r/{subreddit}/{sort}/"
    if sort in ("top", "controversial") and time_filter != "all":
        url += f"?t={time_filter}"

    async with AsyncCamoufox(headless=True, proxy={"server": proxy_url} if proxy_url else None) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector("shreddit-post", timeout=15000)
        except Exception:
            return []
        results = await _scroll_and_collect_shreddit(page, max_results)
    return [{**p, "source": "subreddit"} for p in results]


async def scrape_post_comments(
    proxy_url: str | None,
    post_url: str,
    max_results: int,
    sort: str = "top",
) -> list[dict[str, Any]]:
    url = post_url.rstrip("/") + f"?sort={sort}"

    async with AsyncCamoufox(headless=True, proxy={"server": proxy_url} if proxy_url else None) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector("shreddit-comment", timeout=20000)
        except Exception:
            return []
        results = await _scroll_and_collect(
            page, "shreddit-comment",
            lambda el: _parse_comment_element(el, post_url),
            max_results,
        )
    return [{**c, "source": "comment"} for c in results]


async def scrape_user(
    proxy_url: str | None,
    username: str,
    max_results: int,
    sort: str = "new",
) -> list[dict[str, Any]]:
    username = username.lstrip("u/")
    url = f"https://www.reddit.com/user/{username}/submitted/?sort={sort}"

    async with AsyncCamoufox(headless=True, proxy={"server": proxy_url} if proxy_url else None) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector("shreddit-post", timeout=15000)
        except Exception:
            return []
        results = await _scroll_and_collect_shreddit(page, max_results)
    return [{**p, "source": "user"} for p in results]
