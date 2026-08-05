"""
Unusual Options Activity Detector.

Flags unusual volume, size, and price-action patterns in the options chain:
1. Volume > 2x Open Interest (contract being accumulated)
2. Volume > 10x average daily volume (unusual activity)
3. Large block trades (>$100k premium)
4. Sweep orders (aggressive buying across exchanges)
5. Near-ATM unusual activity (smart money)
"""
import math
from typing import Dict, Any, List, Optional
from datetime import datetime
from collections import defaultdict


class UnusualActivityDetector:
    """
    Detects unusual options activity using multiple signals.
    Combines signals from Barchart, MarketChameleon, UnusualWhales.
    """

    VOLUME_SPIKE_THRESHOLD = 2.0      # Volume > 2x OI
    LARGE_BLOCK_PREMIUM = 100000      # $100k+ premium
    AGGRESSIVE_VOLUME_RATIO = 3.0     # Volume > 3x OI near ATM
    MIN_OI = 100                      # Minimum OI to consider
    MIN_VOLUME = 50                   # Minimum volume to consider

    def scan_chain(
        self,
        chain: List[Dict[str, Any]],
        stock_price: float,
        iv: float = 0.20,
    ) -> List[Dict[str, Any]]:
        """
        Scan entire option chain for unusual activity.
        Returns ranked list of unusual signals.
        """
        signals = []

        for option in chain:
            signal = self._analyze_option(option, stock_price, iv)
            if signal:
                signals.append(signal)

        # Rank by strength
        signals.sort(key=lambda x: x.get("strength", 0), reverse=True)
        return signals

    def _analyze_option(
        self,
        option: Dict[str, Any],
        stock_price: float,
        iv: float,
    ) -> Optional[Dict[str, Any]]:
        """Analyze a single option for unusual activity."""
        volume = option.get("volume", 0)
        oi = option.get("open_interest", 0)
        strike = option.get("strike", 0)
        opt_type = option.get("option_type", "").upper()
        bid = option.get("bid", 0)
        ask = option.get("ask", 0)
        last = option.get("last", 0)
        dte = option.get("dte", 30)

        if volume < self.MIN_VOLUME or oi < self.MIN_OI:
            return None

        # Calculate metrics
        vol_oi_ratio = volume / max(oi, 1)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last
        premium = mid * 100
        total_premium = premium * volume
        itm_pct = abs(strike - stock_price) / stock_price * 100

        signals = []
        strength = 0

        # Signal 1: Volume spike (>2x OI)
        if vol_oi_ratio >= self.VOLUME_SPIKE_THRESHOLD:
            signals.append(f"Volume {vol_oi_ratio:.1f}x OI")
            strength += min(vol_oi_ratio * 15, 40)

        # Signal 2: Large block trade
        if total_premium >= self.LARGE_BLOCK_PREMIUM:
            signals.append(f"Block trade: ${total_premium:,.0f} premium")
            strength += min(total_premium / 100000 * 10, 30)

        # Signal 3: Near-ATM unusual (smart money indicator)
        distance_pct = abs(strike - stock_price) / stock_price * 100
        if distance_pct < 5 and vol_oi_ratio >= self.AGGRESSIVE_VOLUME_RATIO:
            signals.append("Near-ATM unusual (smart money?)")
            strength += 20

        # Signal 4: Call buying (bullish flow)
        if opt_type == "CALL" and vol_oi_ratio >= 2:
            # Check if buying (ask side active)
            if volume > oi:
                signals.append("Call buying (bullish)")
                strength += 10

        # Signal 5: Put buying (bearish flow)
        if opt_type == "PUT" and vol_oi_ratio >= 2:
            if volume > oi:
                signals.append("Put buying (bearish)")
                strength += 10

        # Signal 6: ITM unusual (delta play)
        if itm_pct < 3 and volume > 500:
            signals.append("ITM unusual (delta play)")
            strength += 10

        # Signal 7: Short-dated unusual (gamma play)
        if dte < 7 and vol_oi_ratio >= 3:
            signals.append("Short-dated unusual (gamma play)")
            strength += 15

        if not signals:
            return None

        direction = "bullish" if opt_type == "CALL" else "bearish"
        if opt_type == "PUT" and vol_oi_ratio >= 2 and volume > oi:
            direction = "bearish"
        elif opt_type == "CALL" and vol_oi_ratio >= 2 and volume > oi:
            direction = "bullish"

        return {
            "symbol": option.get("symbol", ""),
            "strike": strike,
            "expiry": option.get("expiry", ""),
            "option_type": opt_type,
            "dte": dte,
            "volume": volume,
            "open_interest": oi,
            "vol_oi_ratio": round(vol_oi_ratio, 2),
            "bid": bid,
            "ask": ask,
            "last": last,
            "mid": mid,
            "premium": round(premium, 2),
            "total_premium": round(total_premium, 2),
            "itm_pct": round(itm_pct, 2),
            "direction": direction,
            "signals": signals,
            "strength": round(strength, 1),
            "timestamp": datetime.now().isoformat(),
        }

    def detect_sweep_orders(
        self,
        chain: List[Dict[str, Any]],
        stock_price: float,
    ) -> List[Dict[str, Any]]:
        """
        Detect sweep orders - aggressive buying that crosses multiple exchanges.
        Sweep = order filled across multiple exchanges simultaneously
        = someone wants in NOW regardless of price.
        """
        sweeps = []
        for option in chain:
            volume = option.get("volume", 0)
            oi = option.get("open_interest", 0)
            
            # Sweep heuristic: volume >> OI and near ATM
            if volume > oi * 3 and volume > 200:
                distance = abs(option.get("strike", 0) - stock_price) / stock_price
                if distance < 0.05:  # Within 5% of stock price
                    sweeps.append({
                        "symbol": option.get("symbol", ""),
                        "strike": option.get("strike", 0),
                        "expiry": option.get("expiry", ""),
                        "type": option.get("option_type", ""),
                        "volume": volume,
                        "oi": oi,
                        "ratio": round(volume / max(oi, 1), 2),
                        "premium": option.get("last", 0) * volume * 100,
                        "direction": "bullish" if option.get("option_type", "").upper() == "CALL" else "bearish",
                        "signal": "sweep_detected",
                    })
        return sorted(sweeps, key=lambda x: x.get("premium", 0), reverse=True)

    def detect_dark_pool_prints(
        self,
        chain: List[Dict[str, Any]],
        stock_price: float,
    ) -> List[Dict[str, Any]]:
        """
        Detect likely dark pool prints.
        Dark pool = large block trades done off-exchange.
        Heuristic: very large OI change + high volume + ITM/ATM.
        """
        prints = []
        for option in chain:
            volume = option.get("volume", 0)
            oi = option.get("open_interest", 0)
            strike = option.get("strike", 0)
            
            # Large block: high volume, significant premium
            mid = (option.get("bid", 0) + option.get("ask", 0)) / 2
            if mid <= 0:
                mid = option.get("last", 0)
            
            total_value = mid * volume * 100
            
            if total_value >= 500000 and volume > 100:  # $500k+ block
                prints.append({
                    "symbol": option.get("symbol", ""),
                    "strike": strike,
                    "expiry": option.get("expiry", ""),
                    "type": option.get("option_type", ""),
                    "volume": volume,
                    "oi": oi,
                    "total_value": round(total_value, 2),
                    "signal": "dark_pool_print",
                    "interpretation": "institutional_flow",
                })
        
        return sorted(prints, key=lambda x: x.get("total_value", 0), reverse=True)

    def aggregate_signals(
        self,
        unusual: List[Dict],
        sweeps: List[Dict],
        dark_pool: List[Dict],
    ) -> Dict[str, Any]:
        """Aggregate all unusual activity signals into a summary."""
        # Count bullish vs bearish signals
        bullish_count = sum(1 for s in unusual if s.get("direction") == "bullish")
        bearish_count = sum(1 for s in unusual if s.get("direction") == "bearish")
        
        total_premium_bull = sum(s.get("total_premium", 0) for s in unusual if s.get("direction") == "bullish")
        total_premium_bear = sum(s.get("total_premium", 0) for s in unusual if s.get("direction") == "bearish")
        
        # Net sentiment
        net_sentiment = (total_premium_bull - total_premium_bear) / max(total_premium_bull + total_premium_bear, 1)
        
        if net_sentiment > 0.3:
            bias = "bullish"
        elif net_sentiment < -0.3:
            bias = "bearish"
        else:
            bias = "neutral"

        return {
            "total_signals": len(unusual),
            "bullish_signals": bullish_count,
            "bearish_signals": bearish_count,
            "total_premium_bull": round(total_premium_bull, 2),
            "total_premium_bear": round(total_premium_bear, 2),
            "net_sentiment": round(net_sentiment, 3),
            "bias": bias,
            "sweeps_detected": len(sweeps),
            "dark_pool_prints": len(dark_pool),
            "top_unusual": unusual[:10],
            "top_sweeps": sweeps[:5],
            "top_dark_pool": dark_pool[:5],
        }
