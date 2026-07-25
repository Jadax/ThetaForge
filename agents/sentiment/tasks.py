"""
Celery tasks for Sentiment Analysis Agent.
Scans Reddit subreddits for community sentiment and trending tickers.
All FREE data via Reddit API (praw library).
"""
import asyncio
import logging
import os
from orchestrator.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="agents.sentiment.tasks.scan_reddit")
def scan_reddit():
    """Scan Reddit for trending tickers and sentiment across options subreddits."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_async_scan_reddit())
    finally:
        loop.close()


async def _async_scan_reddit():
    """Async Reddit sentiment scan."""
    from agents.sentiment.reddit_sentiment import RedditSentimentCollector

    collector = RedditSentimentCollector(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT", "ThetaForge/1.0"),
    )

    # Get trending tickers
    trending = await collector.get_trending_tickers(top_n=20)

    # Get sentiment for top tickers
    sentiment_summaries = {}
    for ticker in list(trending.keys())[:10]:
        summary = await collector.get_sentiment_summary(ticker)
        sentiment_summaries[ticker] = summary

    # Scan individual subreddits for posts
    subreddit_posts = {}
    for sub in ["thetagang", "options", "wallstreetbets", "optionstrading"]:
        posts = await collector.scan_subreddit(sub, limit=30)
        subreddit_posts[sub] = len(posts)

    logger.info(f"Reddit scan complete: {len(trending)} trending tickers")
    return {
        "status": "reddit_scan_complete",
        "trending_tickers": trending,
        "sentiment": sentiment_summaries,
        "subreddit_activity": subreddit_posts,
        "total_tickers_scanned": len(trending),
    }
