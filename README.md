# Reddit Posts Scraper

Scrape Reddit posts, comments, and user histories without needing a Reddit account. Browse any subreddit, fetch comments on specific posts, pull all submissions from a user, or search by keyword — no API keys required for basic use.

## Features

- **Subreddit scraping** — Pull the top, new, hot, or rising posts from any subreddit
- **Post comments** — Fetch all top-level comments from any Reddit post URL
- **User activity** — Get recent submissions from any Reddit username
- **Keyword search** — Search within a subreddit by keyword (global search with Reddit credentials)
- No Reddit account required for subreddit and user scraping
- Export results as JSON, CSV, or Excel
- Returns upvote score, flair, timestamps, comment counts, and more

## Use Cases

- **Market research** — Discover what real people say about products, brands, and topics without bias
- **Sentiment analysis** — Collect post and comment data for NLP or AI training pipelines
- **Lead generation** — Find potential customers actively asking for recommendations in niche subreddits
- **Trend monitoring** — Track which topics are gaining upvotes and discussion in your industry
- **Competitive intelligence** — Monitor mentions of competitor products across communities
- **Content strategy** — Identify the questions your audience is already asking so you can answer them
- **Academic research** — Collect social media data at scale for qualitative or quantitative studies

## How to Use

1. Enter one or more inputs: **Subreddits**, **Post URLs**, **Usernames**, or **Search queries** (with a subreddit filter)
2. Optionally set **Max posts per source** (default 25, up to 500)
3. Optionally enable comment scraping by setting **Max comments per post** > 0
4. Click **Start** and download results as JSON, CSV, or Excel

## Input

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `subreddits` | Array | — | Subreddit names to scrape (e.g. `python`, `investing`) |
| `postUrls` | Array | — | Full Reddit post URLs to fetch comments from |
| `usernames` | Array | — | Reddit usernames to pull submissions from |
| `searchQueries` | Array | — | Keywords to search (add `subredditFilter` for no-credentials search) |
| `maxPostsPerSource` | Number | 25 | Max posts per subreddit or user (up to 500) |
| `maxCommentsPerPost` | Number | 0 | Top-level comments per post (0 = skip comments) |
| `sort` | String | `hot` | `hot`, `new`, `top`, or `rising` |
| `timeFilter` | String | `all` | `hour`, `day`, `week`, `month`, `year`, or `all` |
| `subredditFilter` | String | — | Restrict keyword search to this subreddit (no credentials needed) |

## Output

Each post is saved as a dataset item:

```json
{
  "id": "abc123",
  "url": "https://www.reddit.com/r/python/comments/abc123/...",
  "title": "What's the best library for scraping in 2026?",
  "text": "I've been using BeautifulSoup but looking for alternatives...",
  "author": "dev_user42",
  "subreddit": "python",
  "score": 847,
  "numComments": 134,
  "createdAt": "2026-07-15T09:23:00.000Z",
  "flair": "Question",
  "source": "subreddit"
}
```

Comment items follow the same structure with a `source` of `"comment"`.

## Global Keyword Search

To search Reddit globally by keyword (across all subreddits), provide Reddit OAuth credentials:

1. Go to [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)
2. Create a **script** app (any name, redirect URI: `http://localhost`)
3. Note the **Client ID** (under the app name) and **Client Secret**
4. Enter these plus your Reddit username and password in the actor input

Without credentials, set a `subredditFilter` to restrict keyword search to one community.

## Frequently Asked Questions

**Does it require a Reddit account?**
No — subreddit scraping, post comments, and user histories all work without credentials. Only global keyword search requires Reddit OAuth.

**Is data real-time?**
Results are sourced from [Arctic Shift](https://arctic-shift.photon-reddit.com), a public Reddit archive. Data is typically a few hours old. For real-time data, provide Reddit credentials.

**Can I scrape private subreddits?**
No. Only public subreddits are accessible.

**What's the maximum posts per run?**
Up to 500 posts per subreddit or user per source input. Add multiple sources to scale.

## Pricing

- **$1.00 per 1,000 posts or comments** scraped
- Actor start: $0.00005 per run

Example: 500 posts from 2 subreddits = 1,000 results = **$1.00** total.
