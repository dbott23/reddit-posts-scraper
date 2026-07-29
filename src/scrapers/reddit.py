"""Reddit scraper using Reddit's public RSS/Atom feeds — no credentials required."""

from __future__ import annotations

import asyncio
import datetime
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_ATOM = "http://www.w3.org/2005/Atom"
_DELAY = 2.0  # seconds between paginated requests


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


def _client(proxy_url: str | None = None) -> httpx.AsyncClient:
    proxies = {"http://": proxy_url, "https://": proxy_url} if proxy_url else None
    return httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True, proxies=proxies)


def _parse_entry(entry: ET.Element, source: str) -> dict[str, Any] | None:
    """Parse a single Atom feed entry into a post dict."""
    link_el = entry.find(f"{{{_ATOM}}}link")
    href = link_el.get("href") if link_el is not None else ""
    title_el = entry.find(f"{{{_ATOM}}}title")
    title = (title_el.text or "").strip() if title_el is not None else ""
    if not title or not href:
        return None

    # Extract post ID from URL
    m = re.search(r"/comments/([a-zA-Z0-9]+)/", href)
    post_id = m.group(1) if m else None

    # Author
    author_el = entry.find(f"{{{_ATOM}}}author/{{{_ATOM}}}name")
    author = (author_el.text or "").strip() if author_el is not None else None
    if author == "/u/":
        author = None

    # Published
    pub_el = entry.find(f"{{{_ATOM}}}published")
    created_at = _ts(pub_el.text.strip() if pub_el is not None else None)

    # Subreddit from URL
    sr_m = re.search(r"/r/([^/]+)/", href)
    subreddit = sr_m.group(1) if sr_m else None

    # Content is HTML — parse for score and comment count
    content_el = entry.find(f"{{{_ATOM}}}content")
    content_html = content_el.text or "" if content_el is not None else ""
    soup = BeautifulSoup(content_html, "html.parser")

    score = 0
    num_comments = 0
    text = None
    thumbnail = None

    # Score and comments are in anchor tags at the bottom of content
    for a in soup.find_all("a"):
        t = a.get_text(strip=True)
        m2 = re.match(r"^(\d+)\s+point", t)
        if m2:
            score = _safe_int(m2.group(1))
        m3 = re.match(r"^(\d+)\s+comment", t)
        if m3:
            num_comments = _safe_int(m3.group(1))

    # Selftext from <p> tags (if self post)
    paragraphs = soup.find_all("p")
    if paragraphs:
        text_parts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
        if text_parts:
            text = " ".join(text_parts[:3])  # first few paragraphs

    # Thumbnail from img tag
    img = soup.find("img")
    if img and img.get("src"):
        thumbnail = img.get("src")

    return {
        "id": post_id,
        "url": href,
        "title": title,
        "text": text or None,
        "author": author,
        "subreddit": subreddit,
        "score": score,
        "upvoteRatio": None,
        "numComments": num_comments,
        "createdAt": created_at,
        "isVideo": False,
        "isSelf": None,
        "linkUrl": None,
        "thumbnail": thumbnail,
        "flair": None,
        "awards": 0,
        "source": source,
    }


async def _fetch_feed(client: httpx.AsyncClient, url: str) -> list[ET.Element]:
    resp = await client.get(url)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    return root.findall(f"{{{_ATOM}}}entry")


async def scrape_search(
    proxy_url: str | None,
    query: str,
    max_results: int,
    sort: str = "relevance",
    time_filter: str = "all",
    subreddit: str | None = None,
) -> list[dict[str, Any]]:
    # Reddit search RSS — accessible without credentials
    q = quote_plus(query)
    if subreddit:
        base = f"https://www.reddit.com/r/{subreddit}/search.rss?q={q}&sort={sort}&t={time_filter}&restrict_sr=1"
    else:
        base = f"https://www.reddit.com/search.rss?q={q}&sort={sort}&t={time_filter}&type=link"

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with _client(proxy_url) as client:
        # RSS feeds don't paginate — fetch up to 100 per request
        url = f"{base}&limit={min(100, max_results)}"
        entries = await _fetch_feed(client, url)
        for entry in entries:
            if len(results) >= max_results:
                break
            parsed = _parse_entry(entry, "search")
            if not parsed:
                continue
            uid = parsed.get("id") or parsed.get("url") or ""
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
) -> list[dict[str, Any]]:
    subreddit = subreddit.lstrip("r/")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with _client(proxy_url) as client:
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.rss?limit={min(100, max_results)}"
        if sort in ("top", "controversial") and time_filter != "all":
            url += f"&t={time_filter}"
        entries = await _fetch_feed(client, url)
        for entry in entries:
            if len(results) >= max_results:
                break
            parsed = _parse_entry(entry, "subreddit")
            if not parsed:
                continue
            uid = parsed.get("id") or parsed.get("url") or ""
            if uid not in seen:
                seen.add(uid)
                results.append(parsed)

    return results[:max_results]


async def scrape_post_comments(
    proxy_url: str | None,
    post_url: str,
    max_results: int,
    sort: str = "top",
) -> list[dict[str, Any]]:
    # Comments RSS: https://www.reddit.com/r/{sub}/comments/{id}/.rss
    m = re.search(r"(/r/[^/]+/comments/[a-zA-Z0-9]+)", post_url)
    if not m:
        return []
    path = m.group(1)
    url = f"https://www.reddit.com{path}/.rss?limit={min(100, max_results)}"

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with _client(proxy_url) as client:
        entries = await _fetch_feed(client, url)
        # Skip the first entry which is the post itself
        for entry in entries[1:]:
            if len(results) >= max_results:
                break
            link_el = entry.find(f"{{{_ATOM}}}link")
            href = link_el.get("href") if link_el is not None else ""
            author_el = entry.find(f"{{{_ATOM}}}author/{{{_ATOM}}}name")
            author = (author_el.text or "").strip() if author_el is not None else None
            if not author or author in ("[deleted]", "/u/"):
                continue
            pub_el = entry.find(f"{{{_ATOM}}}published")
            content_el = entry.find(f"{{{_ATOM}}}content")
            content_html = content_el.text or "" if content_el is not None else ""
            soup = BeautifulSoup(content_html, "html.parser")
            body = soup.get_text(separator=" ").strip()
            if not body or body in ("[deleted]", "[removed]"):
                continue
            cid = re.search(r"comment/([a-zA-Z0-9]+)", href)
            uid = cid.group(1) if cid else href
            if uid in seen:
                continue
            seen.add(uid)
            sr_m = re.search(r"/r/([^/]+)/", post_url)
            results.append({
                "id": uid,
                "url": href or post_url,
                "text": body,
                "author": author,
                "subreddit": sr_m.group(1) if sr_m else None,
                "score": 0,  # RSS doesn't expose comment scores
                "createdAt": _ts(pub_el.text.strip() if pub_el is not None else None),
                "isTopLevel": None,
                "postId": re.search(r"/comments/([a-zA-Z0-9]+)/", post_url or "").group(1)
                    if re.search(r"/comments/([a-zA-Z0-9]+)/", post_url or "") else None,
                "source": "comment",
            })

    return results[:max_results]


async def scrape_user(
    proxy_url: str | None,
    username: str,
    max_results: int,
    sort: str = "new",
) -> list[dict[str, Any]]:
    username = username.lstrip("u/")
    url = f"https://www.reddit.com/user/{username}/submitted.rss?limit={min(100, max_results)}&sort={sort}"

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with _client(proxy_url) as client:
        entries = await _fetch_feed(client, url)
        for entry in entries:
            if len(results) >= max_results:
                break
            parsed = _parse_entry(entry, "user")
            if not parsed:
                continue
            uid = parsed.get("id") or parsed.get("url") or ""
            if uid not in seen:
                seen.add(uid)
                results.append(parsed)

    return results[:max_results]
