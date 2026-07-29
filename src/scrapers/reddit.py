"""Reddit scraper — uses OAuth JSON API when credentials are provided, RSS otherwise."""

from __future__ import annotations

import asyncio
import base64
import datetime
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_RSS_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
_ATOM = "http://www.w3.org/2005/Atom"
_OAUTH_BASE = "https://oauth.reddit.com"


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


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

async def fetch_oauth_token(
    client_id: str,
    client_secret: str,
    username: str,
    password: str,
) -> str:
    """Fetch a Reddit OAuth bearer token using password grant."""
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "User-Agent": f"python:apify-reddit-scraper:1.0 (by /u/{username})",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            headers=headers,
            data={
                "grant_type": "password",
                "username": username,
                "password": password,
            },
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


def _rss_client(proxy_url: str | None = None) -> httpx.AsyncClient:
    proxies = {"http://": proxy_url, "https://": proxy_url} if proxy_url else None
    return httpx.AsyncClient(
        headers=_RSS_HEADERS,
        timeout=30,
        follow_redirects=True,
        proxies=proxies,
    )


# ---------------------------------------------------------------------------
# JSON API parser (OAuth path)
# ---------------------------------------------------------------------------

def _parse_post_json(child: dict[str, Any], source: str) -> dict[str, Any] | None:
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
        "createdAt": _ts(
            datetime.datetime.fromtimestamp(
                d.get("created_utc", 0), tz=datetime.timezone.utc
            ).isoformat()
            if d.get("created_utc")
            else None
        ),
        "isVideo": bool(d.get("is_video")),
        "isSelf": bool(d.get("is_self")),
        "linkUrl": d.get("url") if not d.get("is_self") else None,
        "thumbnail": thumbnail,
        "flair": d.get("link_flair_text") or None,
        "awards": _safe_int(d.get("total_awards_received", 0)),
        "source": source,
    }


def _parse_comment_json(child: dict[str, Any], post_url: str) -> dict[str, Any] | None:
    d = child.get("data", {})
    body = (d.get("body") or "").strip()
    if not body or body in ("[deleted]", "[removed]"):
        return None
    author = d.get("author") or None
    if author in ("[deleted]", None):
        return None
    sr_m = re.search(r"/r/([^/]+)/", post_url)
    created_utc = d.get("created_utc")
    return {
        "id": d.get("id"),
        "url": f"https://www.reddit.com{d.get('permalink', '')}",
        "text": body,
        "author": author,
        "subreddit": d.get("subreddit") or (sr_m.group(1) if sr_m else None),
        "score": _safe_int(d.get("score", 0)),
        "createdAt": _ts(
            datetime.datetime.fromtimestamp(
                created_utc, tz=datetime.timezone.utc
            ).isoformat()
            if created_utc
            else None
        ),
        "isTopLevel": d.get("parent_id", "").startswith("t3_"),
        "postId": d.get("link_id", "").removeprefix("t3_") or None,
        "source": "comment",
    }


# ---------------------------------------------------------------------------
# RSS/Atom path (fallback without credentials)
# ---------------------------------------------------------------------------

def _parse_rss_entry(entry: ET.Element, source: str) -> dict[str, Any] | None:
    link_el = entry.find(f"{{{_ATOM}}}link")
    href = link_el.get("href") if link_el is not None else ""
    title_el = entry.find(f"{{{_ATOM}}}title")
    title = (title_el.text or "").strip() if title_el is not None else ""
    if not title or not href:
        return None

    m = re.search(r"/comments/([a-zA-Z0-9]+)/", href)
    post_id = m.group(1) if m else None

    author_el = entry.find(f"{{{_ATOM}}}author/{{{_ATOM}}}name")
    author = (author_el.text or "").strip() if author_el is not None else None
    if author == "/u/":
        author = None

    pub_el = entry.find(f"{{{_ATOM}}}published")
    created_at = _ts(pub_el.text.strip() if pub_el is not None else None)

    sr_m = re.search(r"/r/([^/]+)/", href)
    subreddit = sr_m.group(1) if sr_m else None

    content_el = entry.find(f"{{{_ATOM}}}content")
    content_html = content_el.text or "" if content_el is not None else ""
    soup = BeautifulSoup(content_html, "html.parser")

    score = 0
    num_comments = 0
    text = None
    thumbnail = None

    for a in soup.find_all("a"):
        t = a.get_text(strip=True)
        m2 = re.match(r"^(\d+)\s+point", t)
        if m2:
            score = _safe_int(m2.group(1))
        m3 = re.match(r"^(\d+)\s+comment", t)
        if m3:
            num_comments = _safe_int(m3.group(1))

    paragraphs = soup.find_all("p")
    if paragraphs:
        text_parts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
        if text_parts:
            text = " ".join(text_parts[:3])

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


async def _fetch_rss(client: httpx.AsyncClient, url: str) -> list[ET.Element]:
    resp = await client.get(url)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    return root.findall(f"{{{_ATOM}}}entry")


# ---------------------------------------------------------------------------
# Public scrape functions — accept optional oauth_token + username
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
        async with _oauth_client(oauth_token, oauth_username) as client:
            params: dict[str, Any] = {
                "q": query,
                "sort": sort,
                "t": time_filter,
                "type": "link",
                "limit": min(100, max_results),
            }
            if subreddit:
                params["restrict_sr"] = "1"
                path = f"/r/{subreddit}/search"
            else:
                path = "/search"
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", [])
            for child in children:
                if len(results) >= max_results:
                    break
                parsed = _parse_post_json(child, "search")
                if not parsed:
                    continue
                uid = parsed.get("id") or parsed.get("url") or ""
                if uid not in seen:
                    seen.add(uid)
                    results.append({**parsed, "query": query})
    else:
        q = quote_plus(query)
        if subreddit:
            base = f"https://www.reddit.com/r/{subreddit}/search.rss?q={q}&sort={sort}&t={time_filter}&restrict_sr=1"
        else:
            base = f"https://www.reddit.com/search.rss?q={q}&sort={sort}&t={time_filter}&type=link"
        url = f"{base}&limit={min(100, max_results)}"
        async with _rss_client(proxy_url) as client:
            entries = await _fetch_rss(client, url)
            for entry in entries:
                if len(results) >= max_results:
                    break
                parsed = _parse_rss_entry(entry, "search")
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
            children = resp.json().get("data", {}).get("children", [])
            for child in children:
                if len(results) >= max_results:
                    break
                parsed = _parse_post_json(child, "subreddit")
                if not parsed:
                    continue
                uid = parsed.get("id") or parsed.get("url") or ""
                if uid not in seen:
                    seen.add(uid)
                    results.append(parsed)
    else:
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.rss?limit={min(100, max_results)}"
        if sort in ("top", "controversial") and time_filter != "all":
            url += f"&t={time_filter}"
        async with _rss_client(proxy_url) as client:
            entries = await _fetch_rss(client, url)
            for entry in entries:
                if len(results) >= max_results:
                    break
                parsed = _parse_rss_entry(entry, "subreddit")
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
            # data is a list: [post_listing, comments_listing]
            if len(data) < 2:
                return []
            children = data[1].get("data", {}).get("children", [])
            for child in children:
                if child.get("kind") != "t1":
                    continue
                if len(results) >= max_results:
                    break
                parsed = _parse_comment_json(child, post_url)
                if not parsed:
                    continue
                uid = parsed.get("id") or parsed.get("url") or ""
                if uid not in seen:
                    seen.add(uid)
                    results.append(parsed)
    else:
        path = m.group(0)
        url = f"https://www.reddit.com{path}/.rss?limit={min(100, max_results)}"
        async with _rss_client(proxy_url) as client:
            entries = await _fetch_rss(client, url)
            sr_m = re.search(r"/r/([^/]+)/", post_url)
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
                results.append({
                    "id": uid,
                    "url": href or post_url,
                    "text": body,
                    "author": author,
                    "subreddit": sr_m.group(1) if sr_m else None,
                    "score": 0,
                    "createdAt": _ts(pub_el.text.strip() if pub_el is not None else None),
                    "isTopLevel": None,
                    "postId": post_id,
                    "source": "comment",
                })

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
            children = resp.json().get("data", {}).get("children", [])
            for child in children:
                if len(results) >= max_results:
                    break
                parsed = _parse_post_json(child, "user")
                if not parsed:
                    continue
                uid = parsed.get("id") or parsed.get("url") or ""
                if uid not in seen:
                    seen.add(uid)
                    results.append(parsed)
    else:
        url = f"https://www.reddit.com/user/{username}/submitted.rss?limit={min(100, max_results)}&sort={sort}"
        async with _rss_client(proxy_url) as client:
            entries = await _fetch_rss(client, url)
            for entry in entries:
                if len(results) >= max_results:
                    break
                parsed = _parse_rss_entry(entry, "user")
                if not parsed:
                    continue
                uid = parsed.get("id") or parsed.get("url") or ""
                if uid not in seen:
                    seen.add(uid)
                    results.append(parsed)

    return results[:max_results]
