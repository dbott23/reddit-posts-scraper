# Reddit Posts Scraper

Scrape Reddit posts, comments, and user histories without needing a Reddit account. Browse any subreddit, fetch comments on specific posts, or pull all submissions from a user — no API keys required for basic use.

## What you can scrape

- **Subreddits** — Get the latest posts from any subreddit (e.g. `python`, `MachineLearning`, `investing`)
- **Post comments** — Fetch all top-level comments from any Reddit post URL
- **User posts** — Get recent submissions from any Reddit user
- **Keyword search** — Search posts within a subreddit by keyword (requires Reddit API credentials for global search)

## How to use

1. Set one or more inputs: **Subreddits**, **Post URLs**, **Usernames**, or **Search queries** (with a subreddit filter)
2. Optionally set **Max posts per source** (default 25, up to 500)
3. Optionally enable comments by setting **Max comments per post** > 0
4. Click **Start** and export the results as JSON, CSV, or Excel

## Output fields

Each scraped post includes:

| Field | Description |
|---|---|
| `id` | Reddit post ID |
| `url` | Full URL to the post |
| `title` | Post title |
| `text` | Post body text (for self posts) |
| `author` | Username of the poster |
| `subreddit` | Subreddit name |
| `score` | Upvote score |
| `numComments` | Number of comments |
| `createdAt` | Timestamp (ISO 8601) |
| `isVideo` | Whether the post is a video |
| `isSelf` | Whether it's a text post |
| `linkUrl` | External link URL (for link posts) |
| `thumbnail` | Thumbnail image URL |
| `flair` | Post flair text |
| `source` | Data source (`subreddit`, `search`, `user`, `comment`) |

## Global keyword search

To search Reddit globally by keyword (not restricted to a single subreddit), provide Reddit API credentials:

1. Go to [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)
2. Create a **script** app (any name, redirect URI: `http://localhost`)
3. Enter the **Client ID**, **Client Secret**, your **Reddit username**, and **password** in the actor input

## Limitations

- Results are sourced from [Arctic Shift](https://arctic-shift.photon-reddit.com), a public Reddit archive — data may be up to a few hours old
- Without credentials, keyword search requires a specific subreddit filter
- Maximum 100 posts per subreddit/user per run (Reddit API limit)

## Cost

Pay-per-result pricing: **$1.00 per 1,000 posts** scraped.
