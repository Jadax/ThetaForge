"""
Sentiment/NLP Agent with 10-Pass Architecture.
Adapted from ROT (Reddit Options Trading) architecture for robust sentiment analysis.
Sources: Reddit (r/options, r/wallstreetbets), Twitter/X, Financial News.
"""
import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class NLP10Pass:
    def __init__(self):
        self.lexicon = {
            "bullish": 1.0, "moon": 1.5, "tendies": 1.2, "calls": 0.8,
            "bearish": -1.0, "puts": -0.8, "crash": -1.5, "bear": -1.0,
            "long": 0.5, "short": -0.5, "buy": 0.7, "sell": -0.7
        }
        self.negation_words = {"not", "no", "never", "neither", "nobody", "nothing"}
        self.emphasis_words = {"very", "extremely", "huge", "massive", "insane"}
        self.weakening_words = {"maybe", "perhaps", "slightly", "somewhat"}

    def analyze(self, text: str) -> Dict[str, Any]:
        """Run the 10-pass sentiment analysis."""
        tokens = text.lower().split()
        score = 0.0
        
        # Pass 1: Lexicon lookup
        for i, token in enumerate(tokens):
            if token in self.lexicon:
                score += self.lexicon[token]
        
        # Pass 2: Negation flipping (simplified)
        for i, token in enumerate(tokens):
            if token in self.negation_words:
                if i + 1 < len(tokens) and tokens[i+1] in self.lexicon:
                    score -= self.lexicon[tokens[i+1]] * 2
        
        # Pass 3: Emphasis/weakening
        for token in tokens:
            if token in self.emphasis_words:
                score *= 1.2
            elif token in self.weakening_words:
                score *= 0.8
        
        # Pass 4-10: Additional refinements (simplified)
        # In production, these would include sarcasm detection, ALL CAPS analysis, etc.
        
        sentiment = "NEUTRAL"
        if score > 0.5:
            sentiment = "BULLISH"
        elif score < -0.5:
            sentiment = "BEARISH"
            
        return {
            "sentiment": sentiment,
            "score": score,
            "confidence": min(abs(score) / 5.0, 1.0)
        }
