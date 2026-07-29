"""Reddit Posts Scraper — search posts, scrape subreddits, users, and comments."""

import asyncio
import re

from apify import Actor

from src.scrapers import reddit


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}

        search_queries: list[str] = inp.get("searchQueries") or []
        subreddits: list[str] = inp.get("subreddits") or []
        post_urls: list[str] = inp.get("postUrls") or []
        usernames: list[str] = inp.get("usernames") or []
        max_posts: int = int(inp.get("maxPostsPerSource") or 25)
        max_comments: int = int(inp.get("maxCommentsPerPost") or 0)
        sort: str = inp.get("sort") or "hot"
        search_sort: str = inp.get("searchSort") or "relevance"
        time_filter: str = inp.get("timeFilter") or "all"
        subreddit_filter: str | None = inp.get("subredditFilter") or None

        reddit_client_id: str = (inp.get("redditClientId") or "").strip()
        reddit_client_secret: str = (inp.get("redditClientSecret") or "").strip()
        reddit_username: str = (inp.get("redditUsername") or "").strip()
        reddit_password: str = (inp.get("redditPassword") or "").strip()

        if not any([search_queries, subreddits, post_urls, usernames]):
            await Actor.fail(
                status_message="Provide at least one of: searchQueries, subreddits, postUrls, or usernames."
            )
            return

        # Attempt OAuth if credentials are provided
        oauth_token: str | None = None
        if reddit_client_id and reddit_client_secret and reddit_username and reddit_password:
            try:
                Actor.log.info(f"Fetching Reddit OAuth token for u/{reddit_username}...")
                oauth_token = await reddit.fetch_oauth_token(
                    reddit_client_id, reddit_client_secret, reddit_username, reddit_password
                )
                Actor.log.info("OAuth token obtained — using authenticated Reddit JSON API.")
            except Exception as exc:
                Actor.log.warning(f"OAuth failed ({exc}) — falling back to Arctic Shift API.")
        else:
            Actor.log.info(
                "No Reddit credentials provided — using Arctic Shift public API. "
                "Subreddit and user scraping work without credentials. "
                "Global keyword search requires credentials + a subredditFilter."
            )

        proxy_url = None

        total = 0

        for query in search_queries:
            Actor.log.info(f"Searching: {query!r} (sort={search_sort}, time={time_filter})")
            try:
                posts = await reddit.scrape_search(
                    proxy_url, query, max_posts,
                    sort=search_sort, time_filter=time_filter,
                    subreddit=subreddit_filter,
                    oauth_token=oauth_token,
                    oauth_username=reddit_username,
                )
            except Exception as exc:
                Actor.log.warning(f"Search failed for {query!r}: {exc}")
                continue
            if posts:
                await Actor.push_data(posts)
                total += len(posts)
            Actor.log.info(f"  → {len(posts)} posts (total: {total})")

            if max_comments:
                for post in posts[:10]:
                    url = post.get("url")
                    if url:
                        try:
                            comments = await reddit.scrape_post_comments(
                                proxy_url, url, max_comments,
                                oauth_token=oauth_token, oauth_username=reddit_username,
                            )
                            if comments:
                                await Actor.push_data([{**c, "query": query} for c in comments])
                                total += len(comments)
                        except Exception as exc:
                            Actor.log.warning(f"Comments failed for {url}: {exc}")

        for sub in subreddits:
            sub = sub.lstrip("r/")
            Actor.log.info(f"Scraping r/{sub} (sort={sort}, time={time_filter})")
            try:
                posts = await reddit.scrape_subreddit(
                    proxy_url, sub, max_posts, sort=sort, time_filter=time_filter,
                    oauth_token=oauth_token, oauth_username=reddit_username,
                )
            except Exception as exc:
                Actor.log.warning(f"Subreddit failed for r/{sub}: {exc}")
                continue
            if posts:
                await Actor.push_data(posts)
                total += len(posts)
            Actor.log.info(f"  → {len(posts)} posts from r/{sub} (total: {total})")

            if max_comments:
                for post in posts[:5]:
                    url = post.get("url")
                    if url:
                        try:
                            comments = await reddit.scrape_post_comments(
                                proxy_url, url, max_comments,
                                oauth_token=oauth_token, oauth_username=reddit_username,
                            )
                            if comments:
                                await Actor.push_data(comments)
                                total += len(comments)
                        except Exception as exc:
                            Actor.log.warning(f"Comments failed for {url}: {exc}")

        for url in post_urls:
            Actor.log.info(f"Fetching comments for: {url}")
            if not re.search(r"/r/[^/]+/comments/[a-z0-9]+/", url, re.IGNORECASE):
                Actor.log.warning(f"Could not parse post URL: {url}")
                continue
            try:
                comments = await reddit.scrape_post_comments(
                    proxy_url, url, max_comments or 100,
                    oauth_token=oauth_token, oauth_username=reddit_username,
                )
            except Exception as exc:
                Actor.log.warning(f"Comments failed for {url}: {exc}")
                continue
            if comments:
                await Actor.push_data(comments)
                total += len(comments)
            Actor.log.info(f"  → {len(comments)} comments (total: {total})")

        for username in usernames:
            username = username.lstrip("u/")
            Actor.log.info(f"Scraping u/{username}")
            try:
                posts = await reddit.scrape_user(
                    proxy_url, username, max_posts,
                    oauth_token=oauth_token, oauth_username=reddit_username,
                )
            except Exception as exc:
                Actor.log.warning(f"User posts failed for u/{username}: {exc}")
                continue
            if posts:
                await Actor.push_data(posts)
                total += len(posts)
            Actor.log.info(f"  → {len(posts)} items from u/{username} (total: {total})")

        Actor.log.info(f"Done. Total items pushed: {total}")


if __name__ == "__main__":
    asyncio.run(main())
