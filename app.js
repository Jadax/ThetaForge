(function () {
  "use strict";

  var C = {
    line: "#e5dbc7",
    line2: "#d6c9ad",
    grid: "#eee5d3",
    ink: "#29231b",
    muted: "#877c69",
    accent: "#b0541c",
    accentFill: "rgba(176,84,28,0.13)",
    gain: "#2f7a4a",
    loss: "#ad3f2a",
    panel: "#faf5ea",
  };

  var usd = function (value) {
    if (value == null || isNaN(value)) value = 0;
    return (value >= 0 ? "+" : "-") + "$" + Math.round(Math.abs(value)).toLocaleString("en-US");
  };
  var num = function (value, digits) {
    if (value == null || isNaN(value)) return "n/a";
    return value.toFixed(digits == null ? 1 : digits);
  };
  var pct = function (value) {
    value = Number(value);
    if (isNaN(value)) return "n/a";
    return (value >= 0 ? "+" : "") + value.toFixed(1) + "%";
  };
  var strategyLabel = function (value) {
    var raw = String(value == null ? "" : value);
    var known = {
      etf_rotation: "ETF Rotation",
      equity_momentum: "Equity Momentum",
      bull_put: "Bull Put Spread",
      bear_call: "Bear Call Spread",
      iron_condor: "Iron Condor",
      cash_secured_put: "Cash-Secured Put",
      call_debit: "Call Debit Spread",
      put_debit: "Put Debit Spread"
    };
    if (known[raw]) return known[raw];
    return raw.replaceAll("_", " ").replace(/\b[a-z]/g, function (ch) { return ch.toUpperCase(); });
  };
  var dateLabel = function (value) {
    if (!value) return "n/a";
    var parts = String(value).split("-");
    return parts.length === 3 ? parts[1] + "/" + parts[2] + "/" + parts[0].slice(2) : value;
  };
  var monthLabel = function (value) {
    var parts = String(value).split("-");
    var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    return parts.length === 2 ? months[parseInt(parts[1], 10) - 1] + " " + parts[0] : value;
  };
  var sortStamp = function (trade) {
    var order = trade.order || {};
    return String(order.submitted_at || order.updated_at || trade.timestamp || trade.opened || "");
  };
  var esc = function (value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  };
  var plural = function (n, word) {
    return n + " " + word + (n === 1 ? "" : "s");
  };
  var rMultiple = function (trade) {
    if (trade.status !== "closed") return null;
    var risk = Number(trade.capital_at_risk);
    if (!risk || risk <= 0) return null;
    return Number(trade.net_pnl) / risk;
  };

  /* ---- live marks (VM-refreshed snapshots, delayed-quotes grade) -------- */

  var LIVE_REFETCH_MS = 300000; // trades.json is re-pulled every 5 minutes,
                                 // so an hourly VM snapshot never sits stale
                                 // longer than that on an open tab.

  var priceUsd = function (value, digits) {
    var n = Number(value);
    if (isNaN(n)) return "n/a";
    return "$" + n.toFixed(digits == null ? 2 : digits);
  };
  var liveMark = function (trade) {
    var live = trade.live;
    if (!live || typeof Number(live.price) !== "number" || !isFinite(Number(live.price))) return null;
    if (typeof Number(live.pnl) !== "number" || !isFinite(Number(live.pnl))) return null;
    return live;
  };
  var liveTime = function (live) {
    var match = String((live && live.at) || "").match(/T(\d{2}):(\d{2})/);
    return match ? match[1] + ":" + match[2] + " UTC" : "";
  };
  var rOfPnl = function (trade, pnl) {
    var risk = Number(trade.capital_at_risk);
    if (!risk || risk <= 0) return null;
    return pnl / risk;
  };
  var openMarked = function (trades) {
    return trades.filter(function (t) { return t.status !== "closed" && liveMark(t); });
  };
  var unrealizedOf = function (trade) {
    return Number(liveMark(trade).pnl);
  };

  var allTrades = [];
  var allTradesAccountEquity = 0;
  var engineFilter = "all";
  var statusFilter = "all";
  var subFilter = null;          // { kind: "instrument"|"symbol"|"strategy"|"month", value, label }
  var expandedCards = {};        // trade id -> true while expanded

  var instrumentOf = function (trade) {
    if (trade.instrument_type === "etf" || trade.instrument_type === "stock") {
      return trade.instrument_type;
    }
    return trade.asset_class === "equity" ? "stock" : "option";
  };
  var instrumentLabel = { "option": "OPTIONS", "stock": "STOCKS", "etf": "ETFs" };

  function computeMetrics(trades) {
    var closed = trades.filter(function (trade) { return trade.status === "closed"; });
    var open = trades.filter(function (trade) { return trade.status !== "closed"; });
    var winners = closed.filter(function (trade) { return trade.net_pnl > 0; });
    var losers = closed.filter(function (trade) { return trade.net_pnl <= 0; });
    var grossWin = winners.reduce(function (sum, trade) { return sum + trade.net_pnl; }, 0);
    var grossLoss = Math.abs(losers.reduce(function (sum, trade) { return sum + trade.net_pnl; }, 0));
    var winRate = closed.length ? (winners.length / closed.length) * 100 : null;
    var profitFactor = grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : (closed.length ? 0 : null);
    var avgWin = winners.length ? grossWin / winners.length : 0;
    var avgLoss = losers.length ? grossLoss / losers.length : 0;
    var netPnl = trades.reduce(function (sum, trade) { return sum + (trade.net_pnl || 0); }, 0);
    var expectancy = closed.length ? netPnl / closed.length : null;

    var ordered = trades.slice().sort(function (a, b) { return a.opened.localeCompare(b.opened); });
    var running = 0;
    var cumulative = ordered.map(function (trade) { running += trade.net_pnl || 0; return running; });
    var peak = -Infinity;
    var maxDrawdown = null;
    if (closed.length) {
      maxDrawdown = 0;
      cumulative.forEach(function (value) {
        peak = Math.max(peak, value);
        maxDrawdown = Math.max(maxDrawdown, peak - value);
      });
    }

    var streak = 0;
    for (var index = closed.length - 1; index >= 0; index -= 1) {
      if ((closed[index].net_pnl > 0) === (closed[closed.length - 1].net_pnl > 0)) streak += 1;
      else break;
    }
    var lastClosedWon = closed.length ? closed[closed.length - 1].net_pnl > 0 : null;
    var holds = closed.map(function (t) {
      var opened = Date.parse(String(t.opened || ""));
      var closedAt = Date.parse(String(t.closed || ""));
      if (!isFinite(opened) || !isFinite(closedAt) || closedAt < opened) return null;
      return (closedAt - opened) / 86400000;
    }).filter(function (d) { return d != null; });
    var avgHoldDays = holds.length
      ? holds.reduce(function (sum, d) { return sum + d; }, 0) / holds.length : null;
    var rValues = closed.map(rMultiple).filter(function (value) { return value != null; });
    var avgR = rValues.length
      ? rValues.reduce(function (sum, value) { return sum + value; }, 0) / rValues.length : null;
    var best = closed.length ? closed.slice().sort(function (a, b) { return b.net_pnl - a.net_pnl; })[0] : null;
    var worst = closed.length ? closed.slice().sort(function (a, b) { return a.net_pnl - b.net_pnl; })[0] : null;
    var riskOpen = open.reduce(function (sum, t) { return sum + (Number(t.capital_at_risk) || 0); }, 0);
    var lastActivity = trades.reduce(function (max, t) {
      var d = t.closed || t.opened || "";
      return d > max ? d : max;
    }, "");

    return {
      netPnl: netPnl,
      winRate: winRate,
      profitFactor: profitFactor,
      avgWin: avgWin,
      avgLoss: avgLoss,
      expectancy: expectancy,
      maxDrawdown: maxDrawdown,
      streak: streak,
      lastClosedWon: lastClosedWon,
      winners: winners.length,
      losers: losers.length,
      avgHoldDays: avgHoldDays,
      closed: closed.length,
      open: open.length,
      cumulative: cumulative,
      avgR: avgR,
      best: best,
      worst: worst,
      riskOpen: riskOpen,
      lastActivity: lastActivity,
    };
  }

  function metricCell(label, value, extra, cls) {
    return (
      "<div><small>" + esc(label) + "</small>" +
      "<b class='" + (cls || "") + "'>" + value + "</b>" +
      "<span>" + esc(extra) + "</span></div>"
    );
  }

  function renderHeadline(trades, metrics, accountEquity) {
    var el = document.getElementById("headline");
    if (!trades.length) {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    el.hidden = false;
    var parts = [];
    if (metrics.closed) {
      var cls = metrics.netPnl >= 0 ? "gain" : "loss";
      parts.push(
        "<b class='" + cls + "'>" + esc(usd(metrics.netPnl)) + "</b> realized across " +
        plural(metrics.closed, "closed trade") +
        (metrics.winRate != null
          ? " (" + metrics.winRate.toFixed(0) + "% win rate · " + metrics.winners + " winners vs " +
            metrics.losers + " losers" +
            (metrics.avgHoldDays != null ? " · avg hold " + metrics.avgHoldDays.toFixed(1) + " days" : "") + ")"
          : "")
      );
    } else {
      parts.push("No exits yet, so realized P&amp;L stands at <b>$0</b>");
    }
    if (metrics.open) {
      var riskShare = accountEquity > 0 ? (metrics.riskOpen / accountEquity) * 100 : null;
      parts.push(
        "<b>" + plural(metrics.open, "position") + "</b> still open with <b>" +
        esc(usd(metrics.riskOpen).slice(1)) + "</b> at risk" +
        (riskShare != null ? " (" + riskShare.toFixed(1) + "% of the " + esc(usd(accountEquity).slice(1)) + " book)" : "")
      );
      var marked = openMarked(trades);
      if (marked.length) {
        var unrealized = marked.reduce(function (sum, t) { return sum + unrealizedOf(t); }, 0);
        var unrealizedCls = unrealized >= 0 ? "win" : "loss";
        parts.push(
          "as of the last mark they sit at <b class='" + unrealizedCls + "'>" +
          esc(usd(unrealized)) + "</b> unrealized (" + marked.length +
          "/" + metrics.open + " marked" +
          (liveTime(liveMark(marked[0])) ? " · " + liveTime(liveMark(marked[0])) : "") +
          ")"
        );
      }
    }
    if (metrics.lastActivity) {
      parts.push("last activity " + dateLabel(metrics.lastActivity));
    }
    el.innerHTML = "<p class='eyebrow'>STATE OF THE BOOK</p><p>" + parts.join(" · ") + ".</p>";
  }

  function renderKpis(trades, metrics) {
    var pf = metrics.profitFactor == null ? "n/a"
      : Number.isFinite(metrics.profitFactor) ? metrics.profitFactor.toFixed(2) : "inf";
    var riskShare = allTradesAccountEquity > 0
      ? ((metrics.riskOpen / allTradesAccountEquity) * 100).toFixed(1) + "% of equity"
      : "capital at risk";
    document.getElementById("metric-strip").innerHTML =
      metricCell("NET P&L", esc(usd(metrics.netPnl)),
        plural(trades.length, "trade") + " logged", metrics.netPnl > 0 ? "win" : metrics.netPnl < 0 ? "loss" : "") +
      metricCell("WIN RATE", metrics.winRate != null ? metrics.winRate.toFixed(1) + "%" : "n/a",
        metrics.closed ? metrics.winners + " of " + metrics.closed + " closed" : "no exits yet") +
      metricCell("PROFIT FACTOR", pf, "gross wins / gross losses") +
      metricCell("EXPECTANCY", metrics.expectancy != null ? esc(usd(metrics.expectancy)) : "n/a",
        "avg per closed trade",
        metrics.expectancy == null ? "" : metrics.expectancy >= 0 ? "win" : "loss") +
      metricCell("CAPITAL AT RISK", esc(usd(metrics.riskOpen).slice(1)),
        plural(metrics.open, "open position") + " · " + riskShare) +
      metricCell("MAX DRAWDOWN", metrics.maxDrawdown != null ? "-" + esc(usd(metrics.maxDrawdown).slice(1)) : "n/a",
        metrics.maxDrawdown != null ? "peak-to-trough" : "needs closed trades",
        metrics.maxDrawdown ? "loss" : "");
  }

  function renderCurve(trades, metrics, accountEquity) {
    var closed = trades
      .filter(function (t) { return t.status === "closed"; })
      .sort(function (a, b) { return String(a.closed).localeCompare(String(b.closed)); });

    if (!trades.length) {
      document.getElementById("curve-panel").innerHTML =
        "<div><p class='eyebrow'>PERFORMANCE</p><h2>The equity curve</h2>" +
        "<p>Nothing on the book yet.</p></div>" +
        "<div class='chart-empty'>No trades yet. The curve appears with the first placed trade.</div>";
      return;
    }

    if (!closed.length) {
      renderDeployment(trades, metrics, accountEquity);
      return;
    }

    var start = accountEquity > 0 ? accountEquity : 0;
    var running = 0;
    var series = closed.map(function (t) {
      running += t.net_pnl || 0;
      return {
        date: t.closed,
        value: start + running,
        pnl: t.net_pnl,
        symbol: t.symbol,
        strategy: t.strategy,
      };
    });
    var values = [start].concat(series.map(function (p) { return p.value; }));
    var minV = Math.min.apply(null, values);
    var maxV = Math.max.apply(null, values);
    if (maxV - minV < 2) { maxV += 1; minV -= 1; }
    var span = maxV - minV;
    var W = 640, H = 230, PAD = 30;
    var xPos = function (i) {
      return PAD + (i / Math.max(series.length, 1)) * (W - PAD * 2);
    };
    var yPos = function (v) {
      return H - PAD - ((v - minV) / span) * (H - PAD * 2);
    };
    var pts = [PAD + "," + yPos(start).toFixed(1)].concat(
      series.map(function (p, i) { return xPos(i + 1).toFixed(1) + "," + yPos(p.value).toFixed(1); })
    );
    var points = pts.join(" ");
    var area = PAD + "," + (H - PAD) + " " + points + " " + (W - PAD) + "," + (H - PAD);
    var endValue = series.length ? series[series.length - 1].value : start;
    var endCls = endValue >= start ? C.gain : C.loss;
    var hoverPoints = [{ x: xPos(0), y: yPos(start), date: null, value: start, pnl: null, symbol: null }].concat(
      series.map(function (p, i) {
        return { x: xPos(i + 1), y: yPos(p.value), date: p.date, value: p.value, pnl: p.pnl, symbol: p.symbol, strategy: p.strategy };
      })
    );

    var text =
      "<div><p class='eyebrow'>PERFORMANCE</p><h2>Account equity, exit by exit</h2>" +
      "<p>Chronological and uncompressed. Each point is a closed trade marked at " +
      "the TWS terminal; the dashed line is starting equity of " +
      esc(usd(start).replace("+", "")) + ". Hover the curve for the exact " +
      "values behind each point.</p></div>" +
      "<div class='curve-chart'>" +
      "<svg viewBox='0 0 " + W + " " + H + "' class='curve-svg' role='img' " +
      "aria-label='Account equity curve, " + plural(closed.length, "closed trade") +
      ", ending at " + esc(usd(endValue)) + "'>" +
      "<line x1='" + PAD + "' y1='" + yPos(start).toFixed(1) + "' x2='" + (W - PAD) + "' " +
      "y2='" + yPos(start).toFixed(1) + "' stroke='" + C.line2 + "' stroke-dasharray='4 4'/>" +
      "<polygon points='" + area + "' fill='" + C.accentFill + "'/>" +
      "<polyline points='" + points + "' fill='none' stroke='" + C.accent + "' stroke-width='3' " +
      "stroke-linejoin='round' stroke-linecap='round'/>" +
      "<line class='curve-cross' x1='0' y1='" + PAD + "' x2='0' y2='" + (H - PAD) +
      "' stroke='" + C.line2 + "' stroke-dasharray='3 3' opacity='0'/>" +
      "<circle cx='" + xPos(series.length).toFixed(1) + "' cy='" + yPos(endValue).toFixed(1) +
      "' r='5' fill='" + C.panel + "' stroke='" + endCls + "' stroke-width='3'/>" +
      "<circle class='curve-dot' r='4.5' fill='" + C.panel + "' stroke='" + C.accent +
      "' stroke-width='3' opacity='0'/>" +
      "<text x='" + (W - PAD) + "' y='" + (yPos(maxV) - 7).toFixed(1) + "' fill='" + C.muted +
      "' font-size='11' text-anchor='end'>" + esc(usd(maxV)) + "</text>" +
      "<text x='" + (W - PAD) + "' y='" + (yPos(minV) + 15).toFixed(1) + "' fill='" + C.muted +
      "' font-size='11' text-anchor='end'>" + esc(usd(minV)) + "</text>" +
      "<text x='" + PAD + "' y='" + (H - 8) + "' fill='" + C.muted + "' font-size='10'>" +
      esc(dateLabel(closed[0].closed)) + "</text>" +
      "<text x='" + (W - PAD) + "' y='" + (H - 8) + "' fill='" + C.muted + "' font-size='10' text-anchor='end'>" +
      esc(dateLabel(series[series.length - 1].date)) + "</text>" +
      "</svg>" +
      "<div class='chart-tip' id='curve-tip' hidden></div>" +
      "</div>";
    document.getElementById("curve-panel").innerHTML = text;
    wireCurveHover(hoverPoints);
  }

  function wireCurveHover(points) {
    var wrap = document.querySelector("#curve-panel .curve-chart");
    if (!wrap) return;
    var svg = wrap.querySelector("svg");
    var tip = wrap.querySelector(".chart-tip");
    var cross = wrap.querySelector(".curve-cross");
    var dot = wrap.querySelector(".curve-dot");
    var hide = function () {
      tip.hidden = true;
      cross.setAttribute("opacity", "0");
      dot.setAttribute("opacity", "0");
    };
    wrap.addEventListener("mousemove", function (event) {
      var ctm = svg.getScreenCTM();
      if (!ctm) return;
      var pt = svg.createSVGPoint();
      pt.x = event.clientX;
      pt.y = event.clientY;
      var loc = pt.matrixTransform(ctm.inverse());
      var best = points[0];
      var bestD = Infinity;
      points.forEach(function (p) {
        var d = Math.abs(p.x - loc.x);
        if (d < bestD) { bestD = d; best = p; }
      });
      cross.setAttribute("x1", best.x.toFixed(1));
      cross.setAttribute("x2", best.x.toFixed(1));
      cross.setAttribute("opacity", "1");
      dot.setAttribute("cx", best.x.toFixed(1));
      dot.setAttribute("cy", best.y.toFixed(1));
      dot.setAttribute("opacity", "1");
      if (best.symbol) {
        var cls = best.pnl >= 0 ? "win" : "loss";
        tip.innerHTML =
          "<b>" + esc(dateLabel(best.date)) + " · " + esc(best.symbol) + "</b>" +
          "<span class='" + cls + "'>this exit " + esc(usd(best.pnl)) + "</span>" +
          "<span>equity " + esc(usd(best.value)) + "</span>";
      } else {
        tip.innerHTML =
          "<b>Starting point</b>" +
          "<span>starting equity " + esc(usd(best.value)) + "</span>";
      }
      tip.hidden = false;
      var rect = wrap.getBoundingClientRect();
      var x = event.clientX - rect.left + 16;
      var y = event.clientY - rect.top - 12;
      if (x + tip.offsetWidth > rect.width - 4) {
        x = event.clientX - rect.left - tip.offsetWidth - 16;
      }
      if (y < 2) y = 2;
      tip.style.left = x.toFixed(0) + "px";
      tip.style.top = y.toFixed(0) + "px";
    });
    wrap.addEventListener("mouseleave", hide);
  }

  function renderDeployment(trades, metrics, accountEquity) {
    var bySymbol = {};
    trades.forEach(function (t) {
      if (t.status === "closed") return;
      var sym = String(t.symbol || "?").toUpperCase();
      bySymbol[sym] = (bySymbol[sym] || 0) + (Number(t.capital_at_risk) || 0);
    });
    var rows = Object.keys(bySymbol).map(function (k) { return { sym: k, risk: bySymbol[k] }; })
      .sort(function (a, b) { return b.risk - a.risk; });
    var maxRisk = rows.length ? Math.max.apply(null, rows.map(function (r) { return r.risk; })) : 1;
    var bars = rows.map(function (r) {
      var width = maxRisk > 0 ? Math.max(2, (r.risk / maxRisk) * 100) : 2;
      return (
        "<div class='deploy-row' data-symbol='" + esc(r.sym) + "' role='button' tabindex='0' " +
        "title='Click to filter the journal to " + esc(r.sym) + "'>" +
        "<b>" + esc(r.sym) + "</b>" +
        "<div class='deploy-track'><div class='deploy-fill' style='width:" + width.toFixed(1) + "%'></div></div>" +
        "<span>" + esc(usd(r.risk).slice(1)) + "</span></div>"
      );
    }).join("");
    var riskShare = accountEquity > 0
      ? ((metrics.riskOpen / accountEquity) * 100).toFixed(1) + "% of the " + usd(accountEquity).replace("+", "") + " book"
      : "";
    document.getElementById("curve-panel").innerHTML =
      "<div><p class='eyebrow'>PERFORMANCE</p><h2>Where the risk sits today</h2>" +
      "<p>No position has closed yet, so realized equity is still flat at " +
      esc(usd(accountEquity).replace("+", "")) + ". Each bar is the stop-defined risk " +
      "of an open position" + (riskShare ? ", together " + esc(riskShare) : "") +
      ". The equity curve takes over at the first exit.</p></div>" +
      (rows.length
        ? "<div class='deploy-list'>" + bars + "</div>"
        : "<div class='chart-empty'>Open positions will appear here with their risk.</div>");
  }

  /* ---- KPI breakdowns ------------------------------------------------ */

  function groupTrades(trades) {
    var byInstrument = { "option": [], "stock": [], "etf": [] };
    var byStrategy = {};
    var bySymbol = {};
    trades.forEach(function (trade) {
      var inst = instrumentOf(trade);
      byInstrument[inst].push(trade);
      var strat = trade.strategy || "unknown";
      if (!byStrategy[strat]) byStrategy[strat] = [];
      byStrategy[strat].push(trade);
      var symbol = String(trade.symbol || "?").toUpperCase();
      if (!bySymbol[symbol]) bySymbol[symbol] = [];
      bySymbol[symbol].push(trade);
    });
    return { byInstrument: byInstrument, byStrategy: byStrategy, bySymbol: bySymbol };
  }

  function summarize(trades) {
    var closed = trades.filter(function (t) { return t.status === "closed"; });
    var wins = closed.filter(function (t) { return t.net_pnl > 0; });
    var net = trades.reduce(function (s, t) { return s + (t.net_pnl || 0); }, 0);
    return {
      count: trades.length,
      closed: closed.length,
      wins: wins.length,
      winRate: closed.length ? (wins.length / closed.length) * 100 : null,
      net: net,
    };
  }

  function renderAssetMix(trades) {
    var byInstrument = groupTrades(trades).byInstrument;
    var order = ["option", "stock", "etf"];
    var cards = order.map(function (inst) {
      var group = byInstrument[inst];
      var s = summarize(group);
      var equity = group.reduce(function (sum, t) { return sum + (t.shares || 0); }, 0);
      return (
        "<button class='mix-card' data-instrument='" + inst + "' " +
        (s.count ? "" : "disabled ") +
        "aria-label='Filter the journal to " + esc(instrumentLabel[inst]) + "'>" +
        "<span class='mix-chip " + inst + "'>" + instrumentLabel[inst] + "</span>" +
        "<b>" + s.count + "</b><small>" + (s.count === 1 ? "position" : "positions") + "</small>" +
        "<span class='" + (s.net >= 0 ? "win" : "loss") + " mix-net'>" + esc(usd(s.net)) + "</span>" +
        "<small>net P&amp;L" + (s.winRate != null ? " · " + s.winRate.toFixed(0) + "% win rate (" + s.wins + "/" + s.closed + ")" : " · no closed trades") + "</small>" +
        (inst === "etf" || inst === "stock"
          ? "<small>" + esc(plural(equity, "share")) + " traded across the book</small>" : "") +
        (s.count ? "<small class='mix-hint'>Click to see these trades ↓</small>" : "") +
        "</button>"
      );
    });
    document.getElementById("asset-mix").innerHTML =
      "<div class='journal-list-head'><div><p class='eyebrow'>BREAKDOWN</p>" +
      "<h2>Options, stocks, ETFs: where the book is</h2></div></div>" +
      "<div class='mix-grid'>" + cards.join("") + "</div>";
  }

  function renderMonthlyChart(trades) {
    var el = document.getElementById("monthly-chart");
    var byMonth = {};
    trades.forEach(function (trade) {
      var key = String(trade.closed || trade.opened || "").slice(0, 7);
      if (key.length !== 7) return;
      if (!byMonth[key]) byMonth[key] = { net: 0, closed: 0 };
      if (trade.status === "closed") {
        byMonth[key].net += trade.net_pnl || 0;
        byMonth[key].closed += 1;
      }
    });
    var keys = Object.keys(byMonth).sort();
    var anyClosed = trades.some(function (t) { return t.status === "closed"; });
    if (!keys.length || !anyClosed) {
      el.innerHTML = "<div class='chart-head'><p class='eyebrow'>MONTHLY P&amp;L</p>" +
        "<h2>Profit per month, realized on close</h2></div>" +
        "<div class='chart-empty'>No closed trades yet. Monthly P&amp;L appears here as positions close.</div>";
      return;
    }
    var values = keys.map(function (k) { return byMonth[k].net; });
    var maxAbs = Math.max.apply(null, values.map(function (v) { return Math.abs(v); }).concat([1]));
    var W = 720, H = 250, PAD_L = 46, PAD_R = 12, PAD_T = 16, PAD_B = 30;
    var iw = W - PAD_L - PAD_R, ih = H - PAD_T - PAD_B;
    var step = iw / keys.length;
    var barW = Math.max(5, step * 0.58);
    var zero = PAD_T + ih / 2;
    var yFor = function (v) { return zero - (v / maxAbs) * (ih / 2 - 4); };

    var html = "<svg viewBox='0 0 " + W + " " + H + "' class='monthly-svg' role='img' aria-label='Net P&L by month'>";
    html += "<line x1='" + PAD_L + "' y1='" + zero.toFixed(1) + "' x2='" + (W - PAD_R) + "' y2='" + zero.toFixed(1) +
      "' stroke='" + C.line2 + "' stroke-width='1.5'/>";
    html += "<text x='" + (PAD_L - 8) + "' y='" + (zero - 3).toFixed(1) + "' fill='" + C.muted + "' font-size='10' text-anchor='end'>0</text>";
    [1, -1].forEach(function (sign) {
      var y = zero - sign * (ih / 2 - 4);
      html += "<line x1='" + PAD_L + "' y1='" + y.toFixed(1) + "' x2='" + (W - PAD_R) + "' y2='" + y.toFixed(1) +
        "' stroke='" + C.grid + "'/>";
      html += "<text x='" + (PAD_L - 8) + "' y='" + (y - 3).toFixed(1) + "' fill='" + C.muted + "' font-size='10' text-anchor='end'>" +
        esc(usd(sign * maxAbs)) + "</text>";
    });
    keys.forEach(function (key, i) {
      var v = byMonth[key].net;
      var x = PAD_L + i * step + (step - barW) / 2;
      var y = v >= 0 ? yFor(v) : zero;
      var h = Math.max(Math.abs(v) / maxAbs * (ih / 2 - 4), 2);
      var fill = v >= 0 ? C.gain : C.loss;
      html += "<rect class='mbar' data-month='" + key + "' x='" + x.toFixed(1) + "' y='" + y.toFixed(1) +
        "' width='" + barW.toFixed(1) +
        "' height='" + h.toFixed(1) + "' fill='" + fill + "' rx='2' role='button' " +
        "aria-label='" + esc(monthLabel(key)) + ", " + esc(usd(v)) + ", filter the journal'>" +
        "<title>" + esc(monthLabel(key)) + " · " + esc(usd(v)) +
        " (" + byMonth[key].closed + " closed) — click to filter</title></rect>";
      if (keys.length <= 18) {
        html += "<text x='" + (x + barW / 2).toFixed(1) + "' y='" + (y - 5).toFixed(1) +
          "' fill='" + fill + "' font-size='10' text-anchor='middle' font-weight='bold'>" +
          esc(usd(v)) + "</text>";
      }
      var label = monthLabel(key).split(" ");
      html += "<text x='" + (PAD_L + i * step + step / 2).toFixed(1) + "' y='" + (H - PAD_B + 16).toFixed(1) +
        "' fill='" + C.muted + "' font-size='10' text-anchor='middle'>" + esc(label[0] + " " + label[1].slice(2)) + "</text>";
    });
    html += "</svg>";
    el.innerHTML = "<div class='chart-head'><p class='eyebrow'>MONTHLY P&amp;L</p>" +
      "<h2>Profit per month, realized on close</h2></div>" + html;
  }

  function renderStrategy(trades) {
    var byStrategy = groupTrades(trades).byStrategy;
    var rows = Object.keys(byStrategy).map(function (key) {
      var s = summarize(byStrategy[key]);
      return { key: key, count: s.count, closed: s.closed, net: s.net, winRate: s.winRate };
    }).sort(function (a, b) { return b.net - a.net; });
    var maxAbs = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.net); }).concat([1]));
    var bars = rows.map(function (row) {
      var width = Math.max(2, (Math.abs(row.net) / maxAbs) * 100);
      return (
        "<div class='hbar-row' data-strategy='" + esc(row.key) + "' role='button' tabindex='0' " +
        "title='Click to filter the journal to " + esc(strategyLabel(row.key)) + "'>" +
        "<div class='hbar-label'>" + esc(strategyLabel(row.key)) +
        "<small>" + row.closed + " closed · " + row.count + " total" +
        (row.winRate != null ? " · " + row.winRate.toFixed(0) + "% win rate" : "") + "</small></div>" +
        "<div class='hbar-track'><div class='hbar " + (row.net >= 0 ? "win" : "loss") + "' style='width:" +
        width.toFixed(1) + "%'></div></div>" +
        "<b class='hbar-value " + (row.net >= 0 ? "win" : "loss") + "'>" + esc(usd(row.net)) + "</b></div>"
      );
    }).join("");
    var empty = rows.length ? "" : "<div class='chart-empty'>No trades yet.</div>";
    document.getElementById("analytics-strategy").innerHTML =
      "<div class='journal-list-head'><div><p class='eyebrow'>BY STRATEGY</p>" +
      "<h2>Profit per trade type</h2></div></div>" + empty +
      "<div class='hbar-list'>" + bars + "</div>";
  }

  function tradePrices(trade) {
    var buy = null, sell = null;
    if (trade.asset_class === "equity") {
      buy = trade.entry_price != null ? Number(trade.entry_price)
        : (trade.order && trade.order.average_fill_price != null ? Number(trade.order.average_fill_price) : null);
      sell = (trade.close_order && trade.close_order.average_fill_price != null)
        ? Number(trade.close_order.average_fill_price) : null;
    } else {
      var qty = trade.order && trade.order.quantity ? Number(trade.order.quantity) : 1;
      if (trade.order && trade.order.net_credit != null && Number(trade.order.net_credit) !== 0) {
        buy = Math.abs(Number(trade.order.net_credit)) / qty;
      }
      if (trade.close_order && trade.close_order.cost_to_close != null &&
          Number(trade.close_order.cost_to_close) !== 0) {
        sell = Number(trade.close_order.cost_to_close) / qty;
      }
    }
    return { buy: buy, sell: sell };
  }

  function renderSymbol(trades) {
    var bySymbol = groupTrades(trades).bySymbol;
    var rows = Object.keys(bySymbol).map(function (key) {
      var group = bySymbol[key];
      var s = summarize(group);
      var buys = [], sells = [];
      group.forEach(function (t) {
        var p = tradePrices(t);
        if (p.buy != null) buys.push(p.buy);
        if (p.sell != null) sells.push(p.sell);
      });
      var avg = function (list) {
        return list.length ? list.reduce(function (a, b) { return a + b; }, 0) / list.length : null;
      };
      return {
        key: key,
        instrument: instrumentOf(group[0]),
        count: s.count,
        closed: s.closed,
        winRate: s.winRate,
        net: s.net,
        avgBuy: avg(buys),
        avgSell: avg(sells),
      };
    }).sort(function (a, b) { return b.net - a.net; });
    var maxAbs = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.net); }).concat([1]));
    var body = rows.map(function (row) {
      var width = Math.max(2, (Math.abs(row.net) / maxAbs) * 100);
      var buyCell = row.avgBuy != null ? "$" + row.avgBuy.toFixed(2) : "n/a";
      var sellCell = row.avgSell != null ? "$" + row.avgSell.toFixed(2) : "n/a";
      return (
        "<tr data-symbol='" + esc(row.key) + "' title='Click to filter the journal to " + esc(row.key) + "'>" +
        "<td><b>" + esc(row.key) + "</b></td>" +
        "<td><span class='mix-chip " + row.instrument + "'>" + instrumentLabel[row.instrument] + "</span></td>" +
        "<td>" + row.count + "</td>" +
        "<td>" + (row.winRate != null ? row.winRate.toFixed(0) + "%" : "n/a") + "</td>" +
        "<td>" + buyCell + "</td><td>" + sellCell + "</td>" +
        "<td class='net-cell'><div class='hbar-track'><div class='hbar " +
        (row.net >= 0 ? "win" : "loss") + "' style='width:" + width.toFixed(1) + "%'></div></div>" +
        "<b class='" + (row.net >= 0 ? "win" : "loss") + "'>" + esc(usd(row.net)) + "</b></td></tr>"
      );
    }).join("");
    var empty = rows.length ? "" : "<div class='chart-empty'>No trades yet.</div>";
    document.getElementById("analytics-symbol").innerHTML =
      "<div class='journal-list-head'><div><p class='eyebrow'>BY SYMBOL</p>" +
      "<h2>Profit per name, with average buy and sell prices</h2></div></div>" + empty +
      "<table class='symbol-table'><thead><tr>" +
      "<th>SYMBOL</th><th>TYPE</th><th>TRADES</th><th>WIN RATE</th>" +
      "<th>AVG BUY</th><th>AVG SELL</th><th>NET P&amp;L</th></tr></thead><tbody>" + body + "</tbody></table>";
  }

  function renderExtremes(metrics) {
    var card = function (trade, cls, title) {
      if (!trade) {
        return "<div class='extreme-card " + cls + "'><small>" + title + "</small>" +
          "<b>n/a</b><span>No closed trades yet</span></div>";
      }
      var r = rMultiple(trade);
      return (
        "<div class='extreme-card " + cls + "'><small>" + title + "</small>" +
        "<b class='" + (trade.net_pnl >= 0 ? "win" : "loss") + "'>" + esc(usd(trade.net_pnl)) + "</b>" +
        "<span>" + esc(trade.symbol) + " · " + esc(strategyLabel(trade.strategy)) +
        " · " + esc(dateLabel(trade.closed)) +
        (r != null ? " · " + (r >= 0 ? "+" : "") + r.toFixed(2) + "R" : "") + "</span></div>"
      );
    };
    document.getElementById("analytics-extremes").innerHTML =
      "<div class='journal-list-head'><div><p class='eyebrow'>EXTREMES</p>" +
      "<h2>Best and worst results</h2></div></div>" +
      "<div class='extreme-grid'>" +
      card(metrics.best, "best", "BEST TRADE") +
      card(metrics.worst, "worst", "WORST TRADE") +
      "</div>";
  }

  /* ---- trade cards ----------------------------------------------------- */

  function legLabel(leg) {
    var out = leg.action + " " + leg.type + " " + (leg.strike != null ? leg.strike : "");
    if (leg.expiry) out += " · " + leg.expiry;
    if (leg.dte) out += " · " + leg.dte + "d";
    return out;
  }

  function orderReceipt(trade) {
    if (trade.source !== "ledger" || !trade.order) {
      return "<span class='journal-receipt'>" + esc(trade.timestamp) + "</span>";
    }
    var order = trade.order;
    var bits = [];
    if (order.status) bits.push(order.status);
    if (order.filled != null && order.quantity != null) bits.push(order.filled + "/" + order.quantity + " filled");
    if (order.average_fill_price != null) bits.push("@ " + order.average_fill_price);
    if (order.net_credit != null && Number(order.net_credit) !== 0) bits.push("credit $" + order.net_credit);
    if (trade.close_order) {
      var close = trade.close_order;
      if (close.cost_to_close != null && Number(close.cost_to_close) !== 0) {
        bits.push("closed for $" + close.cost_to_close);
      }
      if (close.realized_pnl != null) bits.push("realized " + usd(close.realized_pnl));
    }
    var when = order.updated_at || order.submitted_at || trade.timestamp;
    return "<span class='journal-receipt'>ledger " +
      esc(trade.ledger_ref || trade.source_id) + " · " + esc(bits.join(" · ")) +
      "<br/>" + esc(when) + "</span>";
  }

  function managementPlan(trade) {
    var plan = trade.management_plan;
    if (!plan) return "";
    var rows = [];
    if (plan.target) rows.push(["TARGET", plan.target]);
    if (plan.stop) rows.push(["STOP", plan.stop]);
    if (plan.time) rows.push(["TIME", plan.time]);
    if (plan.event) rows.push(["EVENT", plan.event]);
    if (!rows.length) return "";
    return "<div class='journal-plan'><small>MANAGEMENT PLAN</small>" +
      rows.map(function (row) {
        return "<span><b>" + esc(row[0]) + "</b>" + esc(row[1]) + "</span>";
      }).join("") + "</div>";
  }

  function condDisplay(key, value) {
    if (value == null || value === "") return null;
    var n;
    switch (key) {
      case "trend":
        if (value === "above_200d") return "Above 200d";
        if (value === "below_200d") return "Below 200d";
        return String(value).replace(/_/g, " ");
      case "pop":
        n = Number(value);
        if (isNaN(n)) return null;
        if (n > 0 && n <= 1) n *= 100;
        return n.toFixed(0) + "%";
      case "expected_move_pct":
        n = Number(value);
        return isNaN(n) ? null : "\u00b1" + n.toFixed(1) + "%";
      case "edge_pct":
        n = Number(value);
        if (isNaN(n)) return null;
        return (n >= 0 ? "+" : "") + String(Math.round(n * 100) / 100) + "%";
      case "vix":
      case "atr_14":
        n = Number(value);
        return isNaN(n) ? null : n.toFixed(2);
      case "price":
        n = Number(value);
        return isNaN(n) ? null : "$" + n.toFixed(2);
      case "score":
      case "composite_score":
      case "confidence_score":
      case "iv_rank":
      case "rsi_14":
      case "adx_14":
        n = Number(value);
        return isNaN(n) ? null : String(Math.round(n * 10) / 10);
      default:
        return String(value).replace(/_/g, " ");
    }
  }

  function entryConditions(trade) {
    var c = trade.conditions;
    if (!c) return "";
    var specs = c.engine === "equity"
      ? [["SCORE", "score"], ["READ", "signal"], ["TREND", "trend"], ["RSI 14", "rsi_14"],
         ["ADX 14", "adx_14"], ["ATR 14", "atr_14"], ["RISK TILT", "risk_tilt"], ["ENTRY", "price"]]
      : [["REGIME", "regime"], ["VIX", "vix"], ["IV RANK", "iv_rank"],
         ["EXPECTED MOVE", "expected_move_pct"], ["SCORE", "composite_score"],
         ["CONFIDENCE", "confidence_score"], ["POP", "pop"], ["EDGE", "edge_pct"]];
    var chips = "";
    specs.forEach(function (spec) {
      var text = condDisplay(spec[1], c[spec[1]]);
      if (text == null) return;
      chips += "<span class='cond-chip'><small>" + esc(spec[0]) + "</small><b>" + esc(text) + "</b></span>";
    });
    if (!chips) return "";
    var rules = "";
    [["ENTRY RULES", c.entry_rules], ["EXIT RULES", c.exit_rules]].forEach(function (pair) {
      if (!pair[1] || !pair[1].length) return;
      rules += "<div class='cond-rules'><b>" + pair[0] + "</b><ul>" +
        pair[1].map(function (rule) { return "<li>" + esc(rule) + "</li>"; }).join("") + "</ul></div>";
    });
    var captured = c.captured_at ? " \u00b7 captured " + esc(dateLabel(c.captured_at)) : "";
    return (
      "<div class='journal-conditions'><small>ENTRY CONDITIONS</small>" +
      "<div class='cond-grid'>" + chips + "</div>" + rules +
      "<p class='cond-note'>The values the quality gates saw when this trade was " +
      "recommended, recorded with the order" + captured + ".</p></div>"
    );
  }

  function headlineFacts(trade, prices, accountEquity) {
    var isEquity = trade.asset_class === "equity";
    var size, openedCell, closedCell;
    if (isEquity) {
      var shares = trade.order && trade.order.quantity != null ? trade.order.quantity : trade.shares;
      size = shares != null ? plural(Number(shares), "share") : "n/a";
      openedCell = prices.buy != null ? "@ $" + prices.buy.toFixed(2) : (trade.entry_price != null ? "@ $" + trade.entry_price : "price n/a");
      closedCell = prices.sell != null ? "@ $" + prices.sell.toFixed(2) : "open, no exit fill";
    } else {
      var qty = trade.order && trade.order.quantity != null ? trade.order.quantity : null;
      size = qty != null ? plural(Number(qty), "contract") : "n/a";
      openedCell = prices.buy != null ? "credit $" + prices.buy.toFixed(2) + "/ctr" : "credit n/a";
      closedCell = prices.sell != null ? "debit $" + prices.sell.toFixed(2) + "/ctr" : "open, no exit fill";
    }
    var riskLine = "n/a";
    if (trade.capital_at_risk != null) {
      riskLine = "risk " + esc(usd(trade.capital_at_risk).slice(1));
      if (accountEquity > 0) {
        riskLine += " · " + ((trade.capital_at_risk / accountEquity) * 100).toFixed(1) + "% of account";
      }
    }
    return (
      "<div class='journal-facts'>" +
      "<span class='fact'><small>" + (isEquity ? "BOUGHT" : "OPENED") + "</small>" +
      "<b>" + esc(dateLabel(trade.opened)) + "</b><span>" + esc(openedCell) + "</span></span>" +
      "<span class='fact'><small>" + (isEquity ? "SOLD" : "CLOSED") + "</small>" +
      "<b>" + (trade.closed ? esc(dateLabel(trade.closed)) : "n/a") + "</b><span>" + esc(closedCell) + "</span></span>" +
      "<span class='fact'><small>SIZE</small><b>" + esc(size) + "</b>" +
      "<span>" + riskLine + "</span></span>" +
      "</div>"
    );
  }

  /* ---- live journey: stop -> entry -> mark -> target, plus the trail ---- */

  function journeyGeometry(trade, mark) {
    var risk = Number(trade.capital_at_risk) || 0;
    if (risk <= 0) return null;
    var isEquity = trade.asset_class === "equity";
    var targetPnl = null, tpPnl = null, labels = {};
    if (isEquity) {
      var p = tradePrices(trade);
      var entry = p.buy != null ? p.buy : (trade.entry_price != null ? Number(trade.entry_price) : null);
      var shares = (trade.order && trade.order.quantity != null) ? Number(trade.order.quantity)
        : (trade.shares != null ? Number(trade.shares) : null);
      if (entry != null && shares > 0 && trade.target_price != null) {
        targetPnl = (Number(trade.target_price) - entry) * shares;
      }
      labels = { low: "stop \u22121R", entry: "entry", target: "+2R target" };
    } else if (trade.max_profit) {
      var maxP = Number(trade.max_profit);
      targetPnl = maxP;
      tpPnl = maxP / 2;
      labels = { low: "defined max loss", entry: "credit received",
                 tp: "50% take-profit", target: "full credit" };
    }
    var now = Number(mark.pnl);
    if (targetPnl == null) targetPnl = risk;   // symmetric fallback keeps a scale
    var lo = -risk, hi = Math.max(targetPnl, now, 0.5 * risk);
    if (hi - lo <= 0) return null;
    var pos = function (value) {
      var frac = (value - lo) / (hi - lo);
      return Math.min(Math.max(frac, 0), 1) * 100;
    };
    return { lo: lo, hi: hi, pos: pos, targetPnl: targetPnl, tpPnl: tpPnl,
             now: now, isEquity: isEquity, labels: labels };
  }

  function journeyBar(trade, mark) {
    var geo = journeyGeometry(trade, mark);
    if (!geo) return "";
    var marker = function (value, label, cls) {
      var x = geo.pos(value);
      var snap = x < 14 ? " snap-left" : x > 86 ? " snap-right" : "";
      return "<div class='journey-marker " + (cls || "") + snap + "' style='left:" + x.toFixed(1) + "%'>" +
        "<i></i><small>" + esc(label) + "</small></div>";
    };
    var entryX = geo.pos(0), nowX = geo.pos(geo.now);
    var fillLeft = Math.min(entryX, nowX);
    var fillWidth = Math.abs(nowX - entryX);
    var fillCls = geo.now >= 0 ? "gain" : "drop";
    var markers = marker(geo.lo, geo.labels.low, "extreme") +
      marker(0, geo.labels.entry, "entry") +
      (geo.tpPnl != null ? marker(geo.tpPnl, geo.labels.tp, "tp") : "") +
      marker(geo.targetPnl, geo.labels.target, "extreme target");
    var markersHtml = mark
      ? markers + marker(geo.now, "now",
          "live" + (Math.abs(nowX - entryX) < 7 ? " tight" : ""))
      : markers;
    return (
      "<div class='journey-bar' role='img' aria-label='Position journey from a " +
      esc(geo.labels.low) + " to a " + esc(geo.labels.target) + ", currently " +
      esc(usd(geo.now)) + "'>" +
      "<div class='journey-track'>" +
      "<div class='journey-fill " + fillCls + "' style='left:" + fillLeft.toFixed(1) +
      "%;width:" + fillWidth.toFixed(1) + "%'></div>" +
      (mark ? "<div class='journey-now-dot' style='left:" + nowX.toFixed(1) + "%'></div>" : "") +
      "</div>" +
      "<div class='journey-layer'>" + markersHtml + "</div>" +
      "</div>"
    );
  }

  function sparklineSvg(trade, mark) {
    var trail = (trade.price_trail || []).filter(function (point) {
      return point && isFinite(Number(point.price));
    });
    if (trail.length < 2) return "";
    var values = trail.map(function (point) { return Number(point.price); });
    var minV = Math.min.apply(null, values);
    var maxV = Math.max.apply(null, values);
    if (maxV - minV < 1e-9) { maxV += 1; minV -= 1; }
    // Pad the range 30% each side; with only a couple of trail points the
    // raw min/max would stretch a 0.1% move across the full chart height
    // and read as a major trend.
    var padV = (maxV - minV) * 0.3;
    minV -= padV;
    maxV += padV;
    var W = 320, H = 52, PAD = 6;
    var xAt = function (i) { return PAD + (i / (trail.length - 1)) * (W - PAD * 2); };
    var yAt = function (v) { return H - PAD - ((v - minV) / (maxV - minV)) * (H - PAD * 2); };
    var pts = values.map(function (v, i) {
      return xAt(i).toFixed(1) + "," + yAt(v).toFixed(1);
    }).join(" ");
    var endY = yAt(values[values.length - 1]);
    var endX = xAt(trail.length - 1);
    var up = mark ? Number(mark.pnl) >= 0
      : values[values.length - 1] >= values[0];
    var color = up ? C.gain : C.loss;
    var entryLine = "";
    var p = tradePrices(trade);
    var entry = p.buy != null ? p.buy : (trade.entry_price != null ? Number(trade.entry_price) : null);
    if (trade.asset_class === "equity" && entry != null && entry >= minV && entry <= maxV) {
      entryLine = "<line x1='" + PAD + "' x2='" + (W - PAD) + "' y1='" + yAt(entry).toFixed(1) +
        "' y2='" + yAt(entry).toFixed(1) + "' stroke='" + C.line2 + "' stroke-dasharray='4 3'/>";
    }
    var lastLabel = liveTime(mark) || "";
    return (
      "<svg class='journey-spark' viewBox='0 0 " + W + " " + H +
      "' preserveAspectRatio='none' role='img' aria-label='" +
      esc(unusedLabel(trade, values)) + "'>" +
      entryLine +
      "<polyline points='" + pts + "' fill='none' stroke='" + color +
      "' stroke-width='2.5' stroke-linejoin='round' stroke-linecap='round'/>" +
      "<circle cx='" + endX.toFixed(1) + "' cy='" + endY.toFixed(1) +
      "' r='3.5' fill='" + color + "'>" +
      (lastLabel ? "<title>" + esc(priceUsd(values[values.length - 1])) + " · " +
        esc(lastLabel) + "</title>" : "") +
      "</circle>" +
      "</svg>"
    );
  }

  var unusedLabel = function (trade, values) {
    var verb = trade.asset_class === "equity" ? "Price" : "Cost to close";
    return verb + " since entry, " + plural(trade.price_trail.length, "mark") +
      ", latest " + priceUsd(values[values.length - 1], trade.asset_class === "equity" ? 2 : 3);
  };

  function journeyPanel(trade) {
    if (trade.status === "closed") return "";
    var mark = liveMark(trade);
    var bar = mark ? journeyBar(trade, mark) : "";
    var spark = sparklineSvg(trade, mark);
    if (!bar && !spark) return "";
    var title = trade.asset_class === "equity" ? "Price journey since entry" : "Mark since entry";
    var trailPoints = (trade.price_trail || []).length;
    var note = "Delayed-venue marks refreshed by the engine" +
      (trailPoints > 1 ? " · " + trailPoints + " snapshots retained" : "") +
      (mark && liveTime(mark) ? " · latest " + esc(liveTime(mark)) : "");
    return (
      "<div class='journey-panel'>" +
      "<small>" + esc(title.toUpperCase()) + "</small>" +
      (bar ? "<div class='journey-bar-wrap'>" + bar + "</div>" : "") +
      (spark ? "<div class='journey-spark-wrap'>" + spark + "</div>" : "") +
      "<p class='journey-note'>" + note + "</p>" +
      "</div>"
    );
  }

  function tradeCard(trade, accountEquity) {
    var won = trade.net_pnl > 0;
    var isLedger = trade.source === "ledger";
    var isEquity = trade.asset_class === "equity";
    var closed = trade.status === "closed";
    var statusLabel = !closed ? "OPEN" : won ? "CLOSED · WIN" : "CLOSED · LOSS";
    var statusClass = !closed ? "open" : won ? "win" : "loss";
    var inst = instrumentOf(trade);
    var prices = tradePrices(trade);
    var isExpanded = !!expandedCards[trade.id];

    var legs;
    var strategyLine;
    if (isEquity) {
      var shares = trade.order && trade.order.quantity != null ? trade.order.quantity : (trade.shares || "");
      legs = "<span class='buy'>BUY " + esc(plural(Number(shares), "share")) +
        (prices.buy != null ? " @ " + esc(prices.buy.toFixed(2)) : "") +
        " · " + esc(dateLabel(trade.opened)) +
        (trade.stop_price != null ? " · stop " + esc(trade.stop_price) : "") +
        (trade.target_price != null ? " · target " + esc(trade.target_price) : "") + "</span>" +
        (prices.sell != null
          ? "<span class='sell'>SELL " + esc(plural(Number(shares), "share")) + " @ " + esc(prices.sell.toFixed(2)) +
            (trade.closed ? " · " + esc(dateLabel(trade.closed)) : "") + "</span>"
          : "");
      strategyLine = "Long " + trade.symbol + " · momentum and trend long" +
        (trade.entry_ivr != null ? " · IVR " + trade.entry_ivr + " at entry" : "");
    } else {
      legs = trade.legs
        .map(function (leg) {
          return "<span class='" + esc(leg.action === "SELL" ? "sell" : "buy") + "'>" +
            esc(legLabel(leg)) + "</span>";
        })
        .join("");
      if (prices.buy != null && prices.sell != null) {
        legs += "<span class='sell'>credit " + esc(prices.buy.toFixed(2)) +
          "/ctr · closed " + esc(prices.sell.toFixed(2)) + "/ctr</span>";
      }
      strategyLine = strategyLabel(trade.strategy);
      if (trade.dte_at_entry) strategyLine += " · " + trade.dte_at_entry + " DTE at entry";
      strategyLine += " · IVR " + (trade.entry_ivr != null ? trade.entry_ivr : "n/a") + " at entry";
      if (trade.expected_move_pct != null) strategyLine += " · expected move ±" + trade.expected_move_pct + "%";
    }

    var tags = trade.tags
      .map(function (tag) { return "<span>#" + esc(tag) + "</span>"; })
      .join("");
    var links = trade.research
      .map(function (link) {
        return "<a href='" + esc(link.url) + "' target='_blank' rel='noreferrer'>" +
          esc(link.label) + " ↗</a>";
      })
      .join("");

    var riskPct = "";
    if (accountEquity > 0 && trade.capital_at_risk) {
      riskPct = " · " + ((trade.capital_at_risk / accountEquity) * 100).toFixed(1) + "% of account";
    }
    var r = rMultiple(trade);
    var mark = liveMark(trade);

    var resultSub = closed
      ? (isEquity
        ? (r != null ? "realized · " + (r >= 0 ? "+" : "") + r.toFixed(2) + "R on risk" : "closed")
        : esc(pct(trade.net_pnl_pct)) + " of max profit")
      : "";

    var pnlBlock;
    if (!closed) {
      if (mark) {
        var uPnl = Number(mark.pnl);
        var uR = rOfPnl(trade, uPnl);
        pnlBlock =
          "<div class='journal-pnl " + (uPnl >= 0 ? "win" : "loss") + "'>" +
          "<small>UNREALIZED</small><b>" + esc(usd(uPnl)) + "</b>" +
          "<span>" + esc(pct(mark.pnl_pct)) + " of risk" +
          (uR != null ? " · " + (uR >= 0 ? "+" : "") + uR.toFixed(2) + "R" : "") +
          (liveTime(mark) ? " · marked " + esc(liveTime(mark)) : "") + "</span></div>";
      } else {
        pnlBlock =
          "<div class='journal-pnl not-yet'><small>POSITION</small><b>open</b>" +
          "<span>waiting for the first price mark</span></div>";
      }
    } else {
      pnlBlock =
        "<div class='journal-pnl " + (won ? "win" : "loss") + "'>" +
        "<small>NET P&amp;L</small><b>" + esc(usd(trade.net_pnl)) + "</b>" +
        "<span>" + esc(resultSub) + "</span></div>";
    }

    var closedOrLiveCell;
    if (closed) {
      closedOrLiveCell = "<span><small>CLOSED</small><b>" + esc(dateLabel(trade.closed)) + "</b></span>";
    } else if (mark) {
      var closeWord = trade.asset_class === "equity" ? "" : " to close";
      closedOrLiveCell =
        "<span><small>CURRENT · " + esc(liveTime(mark)) + "</small><b>" +
        esc(priceUsd(mark.price)) + closeWord + "</b>" +
        "<span class='" + (Number(mark.pnl) >= 0 ? "win" : "loss") + "'>" +
        esc(usd(Number(mark.pnl))) + " since entry</span></span>";
    } else {
      closedOrLiveCell = "<span><small>CURRENT</small><b>n/a</b><span>no mark yet</span></span>";
    }

    var secondMeta;
    if (isEquity) {
      secondMeta = "<span><small>TARGET</small><b>" +
        esc(trade.target_price != null ? "$" + trade.target_price : "n/a") + "</b></span>";
    } else {
      secondMeta = "<span><small>MAX PROFIT</small><b>" +
        esc(trade.max_profit != null ? "$" + Number(trade.max_profit).toFixed(0) : "n/a") + "</b></span>";
    }

    return (
      "<article class='journal-card " + (isExpanded ? "" : "collapsed") + "' data-id='" + esc(trade.id) + "'>" +
      "<button class='journal-card-head' aria-expanded='" + (isExpanded ? "true" : "false") + "'>" +
      "<div class='journal-symbol-row'><span class='sym'>" + esc(trade.symbol) + "</span>" +
      "<span class='engine-chip " + inst + "'>" + instrumentLabel[inst] + "</span>" +
      "<span class='provenance-badge " + (isLedger ? "ledger" : "manual") + "'>" +
      (isLedger ? "TWS LEDGER" : "MANUAL") + "</span>" +
      "<span class='status-pill " + statusClass + "'>" + esc(statusLabel) + "</span>" +
      "<span class='journal-strategy-sub'>" + esc(strategyLabel(trade.strategy || "")) + "</span></div>" +
      headlineFacts(trade, prices, accountEquity) +
      pnlBlock +
      "<span class='chevron' aria-hidden='true'>▾</span>" +
      "</button>" +
      "<div class='journal-card-body'>" +
      "<p class='journal-strategy' style='margin-top:14px'>" + esc(strategyLine) + "</p>" +
      "<div class='journal-legs'>" + legs + "</div>" +
      "<div class='journal-meta'>" +
      "<span><small>OPENED</small><b>" + esc(dateLabel(trade.opened)) + "</b></span>" +
      closedOrLiveCell +
      "<span><small>CAPITAL AT RISK</small><b>" + esc(usd(trade.capital_at_risk).slice(1) + riskPct) + "</b></span>" +
      secondMeta + "</div>" +
      journeyPanel(trade) +
      managementPlan(trade) +
      entryConditions(trade) +
      "<div class='journal-thesis'><small>THE WHY</small><p>" +
      esc((trade.conditions && trade.conditions.reasoning) || trade.reason) + "</p></div>" +
      "<div class='journal-exit'><small>WHAT HAPPENED</small><p>" + esc(trade.exit_note) + "</p></div>" +
      "<div class='journal-footer'>" +
      "<div class='journal-tags'>" + tags + "</div>" +
      "<div class='journal-links'>" + links + orderReceipt(trade) + "</div></div>" +
      "</div></article>"
    );
  }

  function tradeRow(trade) {
    var won = trade.net_pnl > 0;
    var closed = trade.status === "closed";
    var inst = instrumentOf(trade);
    var open = !!expandedCards[trade.id];
    var statusPill = !closed ? "OPEN" : won ? "WIN" : "LOSS";
    var statusClass = !closed ? "open" : won ? "win" : "loss";
    var pnlCell = "";
    var mark = !closed ? liveMark(trade) : null;
    if (mark) {
      var uPnl = Number(mark.pnl);
      var uR = rOfPnl(trade, uPnl);
      pnlCell =
        "<span class='trade-row-pnl " + (uPnl >= 0 ? "win" : "loss") + "'>" +
        esc(usd(uPnl)) +
        "<small>" + esc(pct(mark.pnl_pct)) + " · " +
        (uR != null ? (uR >= 0 ? "+" : "") + uR.toFixed(2) + "R" : esc(liveTime(mark))) +
        "</small></span>";
    } else {
      pnlCell =
        "<span class='trade-row-pnl " + (won ? "win" : "loss") + "'>" +
        esc(usd(trade.net_pnl)) + "</span>";
    }
    return (
      "<div class='trade-row-item" + (open ? " open" : "") + "' data-id='" + esc(trade.id) + "'>" +
      "<button class='trade-row' aria-expanded='" + (open ? "true" : "false") + "'>" +
      "<span class='sym'>" + esc(trade.symbol) + "</span>" +
      "<span class='engine-chip " + inst + "'>" + instrumentLabel[inst] + "</span>" +
      "<span class='trade-row-date'>" + esc(dateLabel(trade.opened)) + "</span>" +
      "<span class='trade-row-strategy'>" + esc(strategyLabel(trade.strategy || "")) + "</span>" +
      "<span class='status-pill " + statusClass + "'>" + esc(statusPill) + "</span>" +
      pnlCell +
      "<span class='chevron' aria-hidden='true'>▾</span>" +
      "</button>" +
      "<div class='trade-row-body'>" + tradeCard(trade, allTradesAccountEquity) + "</div>" +
      "</div>"
    );
  }

  function renderRecaps(trades) {
    var byMonth = {};
    trades.slice().sort(function (a, b) { return a.opened.localeCompare(b.opened); })
      .forEach(function (trade) {
        var key = String(trade.opened).slice(0, 7);
        if (!byMonth[key]) byMonth[key] = [];
        byMonth[key].push(trade);
      });
    var keys = Object.keys(byMonth).sort().reverse();
    if (!keys.length) { document.getElementById("recaps").innerHTML = ""; return; }
    var blocks = keys.map(function (key) {
      var group = byMonth[key];
      var closed = group.filter(function (t) { return t.status === "closed"; });
      var winners = closed.filter(function (t) { return t.net_pnl > 0; });
      var net = group.reduce(function (s, t) { return s + t.net_pnl; }, 0);
      var winRate = closed.length ? (winners.length / closed.length) * 100 : null;
      var biggestWin = closed.length ? Math.max.apply(null, closed.map(function (t) { return t.net_pnl; })) : 0;
      var biggestLoss = closed.length ? Math.min.apply(null, closed.map(function (t) { return t.net_pnl; })) : 0;
      return "<div class='recap-card'><b>" + esc(monthLabel(key)) + "</b>" +
        "<span>" + plural(group.length, "trade") + "</span>" +
        "<span class='" + (net >= 0 ? "win" : "loss") + "'>" + esc(usd(net)) + " net</span>" +
        "<span>" + (winRate != null ? winRate.toFixed(0) + "% win rate" : "no closes yet") + "</span>" +
        (closed.length
          ? "<span class='win'>best " + esc(usd(Math.max(biggestWin, 0))) + "</span>" +
            "<span class='loss'>worst " + esc(usd(Math.min(biggestLoss, 0))) + "</span>"
          : "");
    });
    document.getElementById("recaps").innerHTML =
      "<div class='journal-list-head'><div><p class='eyebrow'>MONTH IN REVIEW</p>" +
      "<h2>Weeks and months, in review</h2></div></div><div class='recap-grid'>" + blocks.join("") + "</div>";
  }

  /* ---- filters ---------------------------------------------------------- */

  function setEngineTab(filter) {
    engineFilter = filter;
    var bar = document.getElementById("journal-filters");
    Array.prototype.forEach.call(bar.querySelectorAll("[data-filter]"), function (tab) {
      tab.setAttribute("aria-selected", tab.getAttribute("data-filter") === filter ? "true" : "false");
    });
  }

  function setStatusFilter(filter) {
    statusFilter = filter;
    var bar = document.getElementById("journal-status-filters");
    Array.prototype.forEach.call(bar.querySelectorAll("[data-status]"), function (tab) {
      tab.setAttribute("aria-selected", tab.getAttribute("data-status") === filter ? "true" : "false");
    });
  }

  function renderSubFilter() {
    var el = document.getElementById("sub-filter");
    if (!subFilter) {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    el.hidden = false;
    el.innerHTML = "Showing <b>" + esc(subFilter.label) + "</b>" +
      "<button type='button' aria-label='Clear filter' title='Clear filter'>✕</button>";
  }

  function clearSubFilter() {
    subFilter = null;
    renderSubFilter();
    renderList();
  }

  function applySubFilter(kind, value, label) {
    subFilter = { kind: kind, value: value, label: label };
    setEngineTab("all");
    renderSubFilter();
    renderList();
    scrollToJournal();
  }

  function wireFilters() {
    var bar = document.getElementById("journal-filters");
    renderFilterCounts();
    bar.addEventListener("click", function (event) {
      var button = event.target.closest("[data-filter]");
      if (!button) return;
      setEngineTab(button.getAttribute("data-filter"));
      subFilter = null;
      renderSubFilter();
      renderList();
    });
    document.getElementById("journal-status-filters").addEventListener("click", function (event) {
      var chip = event.target.closest("[data-status]");
      if (!chip) return;
      setStatusFilter(chip.getAttribute("data-status"));
      renderList();
    });
    document.getElementById("sub-filter").addEventListener("click", function (event) {
      if (event.target.tagName === "BUTTON") clearSubFilter();
    });
    document.getElementById("asset-mix").addEventListener("click", function (event) {
      var card = event.target.closest("[data-instrument]");
      if (!card || card.disabled) return;
      var inst = card.getAttribute("data-instrument");
      applySubFilter("instrument", inst, instrumentLabel[inst]);
    });
    document.getElementById("analytics-symbol").addEventListener("click", function (event) {
      var row = event.target.closest("tr[data-symbol]");
      if (!row) return;
      var sym = row.getAttribute("data-symbol");
      applySubFilter("symbol", sym, sym);
    });
    document.getElementById("analytics-strategy").addEventListener("click", function (event) {
      var row = event.target.closest("[data-strategy]");
      if (!row) return;
      var key = row.getAttribute("data-strategy");
      applySubFilter("strategy", key, strategyLabel(key));
    });
    document.getElementById("monthly-chart").addEventListener("click", function (event) {
      var bar = event.target.closest("[data-month]");
      if (!bar) return;
      var key = bar.getAttribute("data-month");
      applySubFilter("month", key, monthLabel(key));
    });
    document.getElementById("curve-panel").addEventListener("click", function (event) {
      var row = event.target.closest("[data-symbol]");
      if (!row) return;
      var sym = row.getAttribute("data-symbol");
      applySubFilter("symbol", sym, sym);
    });
    document.getElementById("journal-list").addEventListener("click", function (event) {
      var row = event.target.closest(".trade-row");
      if (row) {
        var item = row.closest(".trade-row-item");
        var opened = item.classList.toggle("open");
        row.setAttribute("aria-expanded", opened ? "true" : "false");
        expandedCards[item.getAttribute("data-id")] = opened;
        syncToggleButton();
        return;
      }
      var head = event.target.closest(".journal-card-head");
      if (!head) return;
      var card = head.closest(".journal-card");
      var id = card.getAttribute("data-id");
      var open = card.classList.toggle("collapsed") === false;
      expandedCards[id] = open;
      head.setAttribute("aria-expanded", open ? "true" : "false");
      syncToggleButton();
    });
    document.getElementById("toggle-cards").addEventListener("click", function () {
      var items = document.querySelectorAll("#journal-list .trade-row-item");
      var anyCollapsed = Array.prototype.some.call(items, function (i) {
        return !i.classList.contains("open");
      });
      Array.prototype.forEach.call(items, function (item) {
        var id = item.getAttribute("data-id");
        var rowBtn = item.querySelector(".trade-row");
        if (anyCollapsed) {
          item.classList.add("open");
          expandedCards[id] = true;
        } else {
          item.classList.remove("open");
          expandedCards[id] = false;
        }
        if (rowBtn) rowBtn.setAttribute("aria-expanded", anyCollapsed ? "true" : "false");
      });
      syncToggleButton();
    });
  }

  function scrollToJournal() {
    var target = document.getElementById("journal-list");
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function syncToggleButton() {
    var btn = document.getElementById("toggle-cards");
    var items = document.querySelectorAll("#journal-list .trade-row-item");
    if (!items.length) { btn.hidden = true; return; }
    btn.hidden = false;
    var anyExpanded = Array.prototype.some.call(items, function (i) {
      return i.classList.contains("open");
    });
    btn.textContent = anyExpanded ? "Collapse all" : "Expand all";
    btn.setAttribute("aria-pressed", anyExpanded ? "true" : "false");
  }

  function renderFilterCounts() {
    var counts = { "all": allTrades.length, "options": 0, "stocks": 0, "etfs": 0 };
    var statusCounts = { "all": allTrades.length, "open": 0, "winners": 0, "losers": 0 };
    allTrades.forEach(function (t) {
      var inst = instrumentOf(t);
      if (inst === "option") counts.options += 1;
      else if (inst === "etf") counts.etfs += 1;
      else counts.stocks += 1;
      if (t.status !== "closed") statusCounts.open += 1;
      else if ((Number(t.net_pnl) || 0) > 0) statusCounts.winners += 1;
      else statusCounts.losers += 1;
    });
    Array.prototype.forEach.call(
      document.querySelectorAll("#journal-filters [data-filter], #journal-status-filters [data-status]"),
      function (tab) {
        var group = tab.getAttribute("data-filter") != null ? counts : statusCounts;
        var key = tab.getAttribute("data-filter") != null ? tab.getAttribute("data-filter") : tab.getAttribute("data-status");
        var count = tab.querySelector(".count");
        if (count) count.remove();
        tab.insertAdjacentHTML("beforeend", "<span class='count'>" + group[key] + "</span>");
      }
    );
  }

  function tradeMonth(trade) {
    return String(trade.closed || trade.opened || "").slice(0, 7);
  }

  function renderList() {
    var list = document.getElementById("journal-list");
    var filtered = allTrades;
    if (engineFilter !== "all") {
      var wanted = { options: "option", stocks: "stock", etfs: "etf" }[engineFilter];
      filtered = filtered.filter(function (trade) { return instrumentOf(trade) === wanted; });
    }
    if (subFilter && subFilter.kind === "instrument") {
      filtered = filtered.filter(function (trade) { return instrumentOf(trade) === subFilter.value; });
    }
    if (subFilter && subFilter.kind === "symbol") {
      filtered = filtered.filter(function (trade) {
        return String(trade.symbol || "").toUpperCase() === subFilter.value;
      });
    }
    if (subFilter && subFilter.kind === "strategy") {
      filtered = filtered.filter(function (trade) { return trade.strategy === subFilter.value; });
    }
    if (subFilter && subFilter.kind === "month") {
      filtered = filtered.filter(function (trade) { return tradeMonth(trade) === subFilter.value; });
    }
    if (statusFilter === "open") {
      filtered = filtered.filter(function (trade) { return trade.status !== "closed"; });
    } else if (statusFilter === "winners") {
      filtered = filtered.filter(function (trade) {
        return trade.status === "closed" && (Number(trade.net_pnl) || 0) > 0;
      });
    } else if (statusFilter === "losers") {
      filtered = filtered.filter(function (trade) {
        return trade.status === "closed" && (Number(trade.net_pnl) || 0) <= 0;
      });
    }
    var instCounts = { option: 0, stock: 0, etf: 0 };
    filtered.forEach(function (trade) { instCounts[instrumentOf(trade)] += 1; });
    document.getElementById("sort-note").textContent =
      "Newest first · " + filtered.length + " entries · " +
      instCounts.option + " options / " + instCounts.stock + " stocks / " + instCounts.etf + " ETFs";
    if (!filtered.length) {
      var engineName = { stocks: "Stocks", etfs: "ETFs", options: "Options" }[engineFilter] || null;
      var statusName = { open: "open", winners: "winners", losers: "losers" }[statusFilter] || null;
      var scope = subFilter ? " for " + subFilter.label : "";
      var base;
      if (engineName && statusName) base = "No " + statusName + " among " + engineName.toLowerCase();
      else if (statusName) base = "No " + statusName;
      else base = "No " + (engineName || "Trades").toLowerCase();
      list.innerHTML =
        "<div class='journal-empty'><b>" + esc(base + scope + " yet.") + "</b><br/>" +
        "Clear the filter above, or wait for the next recommendation to be placed on paper. " +
        "Every trade lands here with its thesis, structure, and receipt.</div>";
      syncToggleButton();
      return;
    }
    var ordered = filtered.slice().sort(function (a, b) { return sortStamp(a).localeCompare(sortStamp(b)); });
    list.innerHTML = ordered.reverse()
      .map(function (trade) { return tradeRow(trade); })
      .join("");
    syncToggleButton();
  }

  function render(data) {
    var trades = data.trades || [];
    allTrades = trades;
    allTradesAccountEquity = data.account_equity || 0;
    var trader = data.trader || {};
    var metrics = computeMetrics(trades);
    var accountEquity = data.account_equity || 0;

    renderHeadline(trades, metrics, accountEquity);
    renderKpis(trades, metrics);
    renderCurve(trades, metrics, accountEquity);
    renderAssetMix(trades);
    renderMonthlyChart(trades);
    renderStrategy(trades);
    renderSymbol(trades);
    renderExtremes(metrics);
    renderRecaps(trades);

    document.getElementById("journal-sub").textContent =
      (trader.tagline || "") + " · " + (trader.name || "") + " (" + (trader.handle || "") + ")" +
      " · journal updated " + (data.as_of || "") +
      ". Every placed trade appears here, winners and losers. Nothing is filtered.";

    if (data.verification && data.verification.ledger_sha) {
      var verifyText =
        "Every ledger entry above is derived from the TWS paper-order ledger " +
        "(sha " + data.verification.ledger_sha.slice(0, 12) + ") on " + (data.as_of || "") +
        ". Recomputable, never edited by hand.";
      var carried = Number(data.verification.entries_carried) || 0;
      if (carried > 0 && data.verification.prior_ledger_sha) {
        verifyText += " " + carried + " earlier " + plural(carried, "entry") +
          " carried from the prior ledger (sha " +
          String(data.verification.prior_ledger_sha).slice(0, 12) +
          ") and kept verbatim.";
      }
      document.getElementById("verify-note").textContent = verifyText;
    }

    renderList();
    wireFilters();
  }

  function renderEmpty() {
    document.getElementById("headline").hidden = true;
    document.getElementById("metric-strip").innerHTML =
      "<div><small>NET P&L</small><b>+$0</b><span>0 trades logged</span></div>" +
      "<div><small>WIN RATE</small><b>n/a</b><span>no exits yet</span></div>" +
      "<div><small>PROFIT FACTOR</small><b>n/a</b><span>gross wins / gross losses</span></div>" +
      "<div><small>EXPECTANCY</small><b>n/a</b><span>avg per closed trade</span></div>" +
      "<div><small>CAPITAL AT RISK</small><b>$0</b><span>0 open positions</span></div>" +
      "<div><small>MAX DRAWDOWN</small><b>n/a</b><span>needs closed trades</span></div>";
    ["curve-panel", "asset-mix", "monthly-chart", "analytics-strategy", "analytics-symbol", "analytics-extremes"]
      .forEach(function (id) {
        document.getElementById(id).innerHTML =
          "<div class='chart-empty'>No trades yet. The breakdowns appear here once positions are placed.</div>";
      });
    document.getElementById("journal-list").innerHTML =
      "<div class='journal-empty'><b>No trades yet.</b><br/>" +
      "The first ThetaForge recommendation you place on TWS will appear here, " +
      "with the thesis, the structure, and the receipt.</div>";
    syncToggleButton();
  }

  var lastPayload = null;
  function loadJournal() {
    fetch("./trades.json", { cache: "no-store" })
      .then(function (response) { return response.text(); })
      .then(function (text) {
        if (text === lastPayload) return;   // nothing changed VM-side; skip re-render
        lastPayload = text;
        render(JSON.parse(text));
      })
      .catch(renderEmpty);
  }
  loadJournal();
  // The VM bakes fresh price marks into trades.json hourly (journal timer),
  // and a five-minute same-origin re-fetch keeps an open tab no more than
  // ~5 min behind whatever the latest published snapshot is.
  setInterval(loadJournal, LIVE_REFETCH_MS);
})();
