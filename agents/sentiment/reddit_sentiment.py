"""
Reddit Sentiment Collector.
FREE data source from r/thetagang, r/options, r/wallstreetbets,
r/optionstrading, r/TradingEdge, r/algotrading, r/quant, r/ValueInvesting.
Uses PRAW (Python Reddit API Wrapper) - free with Reddit account.
Adapted from general social sentiment analysis patterns.
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import Counter
import re

logger = logging.getLogger(__name__)

SUBREDDITS_BY_FOCUS = {
    "thetagang": ["wheel", "credit spread", "iron condor", "CSP", "covered call", "theta"],
    "options": ["puts", "calls", "straddle", "strangle", "earnings"],
    "wallstreetbets": ["yolo", "tendies", "moon", "puts", "calls", "0DTE"],
    "optionstrading": ["spread", "策略", "analysis", "IV", "Greeks"],
    "algotrading": ["quant", "backtest", "strategy", "algorithm"],
    "quant": ["factor", "model", "portfolio", "risk"],
    "ValueInvesting": ["LEAPS", "value", "intrinsic", "margin of safety"],
}


class RedditSentimentCollector:
    """
    Collects and analyzes sentiment from options-related subreddits.
    Requires free Reddit API credentials (https://www.reddit.com/prefs/apps).
    """

    def __init__(self, client_id: str = None, client_secret: str = None, user_agent: str = None):
        self.reddit = None
        try:
            import praw
            if client_id and client_secret:
                self.reddit = praw.Reddit(
                    client_id=client_id,
                    client_secret=client_secret,
                    user_agent=user_agent or "ThetaForge/1.0",
                )
                logger.info("Reddit API connected successfully.")
        except ImportError:
            logger.warning("praw not installed. Reddit sentiment disabled.")
        except Exception as e:
            logger.warning(f"Reddit connection failed: {e}")

    async def scan_subreddit(
        self, subreddit: str, limit: int = 50, time_filter: str = "day"
    ) -> List[Dict[str, Any]]:
        """Scan a subreddit for recent posts and comments."""
        if not self.reddit:
            return []

        try:
            sub = self.reddit.subreddit(subreddit)
            posts = []
            for post in sub.new(limit=limit):
                posts.append({
                    "title": post.title,
                    "selftext": post.selftext[:500] if post.selftext else "",
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "created_utc": datetime.fromtimestamp(post.created_utc),
                    "url": post.url,
                    "flair": post.link_flair_text or "",
                })
            return posts
        except Exception as e:
            logger.error(f"Reddit scan failed for r/{subreddit}: {e}")
            return []

    async def get_trending_tickers(self, top_n: int = 20) -> Dict[str, int]:
        """Find most-mentioned tickers across options subreddits."""
        ticker_pattern = re.compile(r'\b[A-Z]{1,5}\b')
        all_text = []

        for sub in ["thetagang", "options", "wallstreetbets", "optionstrading"]:
            posts = await self.scan_subreddit(sub, limit=100)
            for p in posts:
                all_text.append(p["title"] + " " + p["selftext"])

        # Extract tickers (filter common non-ticker words)
        stopwords = {
            "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HER",
            "WAS", "ONE", "OUR", "OUT", "HAS", "HIS", "HOW", "MAN", "NEW", "NOW",
            "OLD", "SEE", "WAY", "WHO", "DID", "GET", "HIM", "LET", "SAY", "SHE",
            "TOO", "USE", "ATH", "OTM", "ITM", "ATM", "CSP", "CC", "LEAPS", "DD",
            "IV", "OI", "P/L", "GTC", "FOMC", "CPI", "GDP", "ETF", "SPY", "QQQ",
        }
        tickers = Counter()
        for text in all_text:
            matches = ticker_pattern.findall(text)
            for m in matches:
                if m not in stopwords and len(m) >= 2:
                    tickers[m] += 1

        return dict(tickers.most_common(top_n))

    async def get_sentiment_summary(self, ticker: str) -> Dict[str, Any]:
        """Get sentiment summary for a specific ticker from Reddit."""
        bullish_count = 0
        bearish_count = 0
        total_mentions = 0

        bullish_words = {"bullish", "calls", "moon", "long", "buy", "up", "calls", "yolo"}
        bearish_words = {"bearish", "puts", "crash", "short", "sell", "down", "puts", "dump"}

        for sub in ["thetagang", "options", "wallstreetbets"]:
            posts = await self.scan_subreddit(sub, limit=50)
            for p in posts:
                text = (p["title"] + " " + p["selftext"]).lower()
                if ticker.lower() in text:
                    total_mentions += 1
                    words = set(text.split())
                    if words & bullish_words:
                        bullish_count += 1
                    if words & bearish_words:
                        bearish_count += 1

        total = max(bullish_count + bearish_count, 1)
        sentiment_score = (bullish_count - bearish_count) / total

        return {
            "ticker": ticker,
            "mentions": total_mentions,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "sentiment_score": round(sentiment_score, 3),
            "sentiment_label": "BULLISH" if sentiment_score > 0.2 else "BEARISH" if sentiment_score < -0.2 else "NEUTRAL",
        }
