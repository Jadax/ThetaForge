(function () {
  "use strict";

  var usd = function (value) {
    return (value >= 0 ? "+" : "-") + "$" + Math.abs(value).toFixed(0);
  };
  var pct = function (value) {
    return (value >= 0 ? "+" : "") + value.toFixed(1) + "%";
  };
  var strategyLabel = function (value) {
    return String(value).replaceAll("_", " ");
  };
  var dateLabel = function (value) {
    var parts = String(value).split("-");
    return parts.length === 3 ? parts[1] + "/" + parts[2] + "/" + parts[0].slice(2) : value;
  };
  var monthLabel = function (value) {
    var parts = String(value).split("-");
    var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    return parts.length === 2 ? months[parseInt(parts[1], 10) - 1] + " " + parts[0] : value;
  };
  var esc = function (value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  };

  function computeMetrics(trades) {
    var closed = trades.filter(function (trade) { return trade.status === "closed"; });
    var winners = closed.filter(function (trade) { return trade.net_pnl > 0; });
    var losers = closed.filter(function (trade) { return trade.net_pnl <= 0; });
    var grossWin = winners.reduce(function (sum, trade) { return sum + trade.net_pnl; }, 0);
    var grossLoss = Math.abs(losers.reduce(function (sum, trade) { return sum + trade.net_pnl; }, 0));
    var winRate = closed.length ? (winners.length / closed.length) * 100 : 0;
    var profitFactor = grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0;
    var avgWin = winners.length ? grossWin / winners.length : 0;
    var avgLoss = losers.length ? grossLoss / losers.length : 0;
    var netPnl = trades.reduce(function (sum, trade) { return sum + trade.net_pnl; }, 0);
    var expectancy = closed.length ? netPnl / closed.length : 0;

    var ordered = trades.slice().sort(function (a, b) { return a.opened.localeCompare(b.opened); });
    var running = 0;
    var cumulative = ordered.map(function (trade) { running += trade.net_pnl; return running; });
    var peak = -Infinity;
    var maxDrawdown = 0;
    cumulative.forEach(function (value) {
      peak = Math.max(peak, value);
      maxDrawdown = Math.max(maxDrawdown, peak - value);
    });
    var drawdownFromPeak = cumulative.length ? Math.max(peak - cumulative[cumulative.length - 1], 0) : 0;

    var streak = 0;
    for (var index = closed.length - 1; index >= 0; index -= 1) {
      if ((closed[index].net_pnl > 0) === (closed[closed.length - 1].net_pnl > 0)) streak += 1;
      else break;
    }
    var lastClosedWon = closed.length ? closed[closed.length - 1].net_pnl > 0 : null;
    return {
      netPnl: netPnl,
      winRate: winRate,
      profitFactor: profitFactor,
      avgWin: avgWin,
      avgLoss: avgLoss,
      expectancy: expectancy,
      maxDrawdown: maxDrawdown,
      drawdownFromPeak: drawdownFromPeak,
      streak: streak,
      lastClosedWon: lastClosedWon,
      winners: winners.length,
      closed: closed.length,
      cumulative: cumulative,
    };
  }

  function metricCell(label, value, extra, cls) {
    return (
      "<div><small>" + esc(label) + "</small>" +
      "<b class='" + (cls || "") + "'>" + value + "</b>" +
      "<span>" + esc(extra) + "</span></div>"
    );
  }

  function renderCurve(metrics, accountEquity) {
    var cumulative = metrics.cumulative;
    var start = accountEquity > 0 ? accountEquity : 0;
    var W = 640, H = 220, PAD = 26;
    var minV = start + Math.min(0, ...cumulative);
    var maxV = start + Math.max(...cumulative, 1);
    var span = maxV - minV || 1;
    var xPos = function (index) {
      return PAD + (index / Math.max(cumulative.length - 1, 1)) * (W - PAD * 2);
    };
    var yPos = function (value) {
      return H - PAD - ((value - minV) / span) * (H - PAD * 2);
    };
    var points = cumulative
      .map(function (value, index) { return xPos(index).toFixed(1) + "," + yPos(start + value).toFixed(1); })
      .join(" ");
    var area = PAD + "," + (H - PAD) + " " + points + " " + (W - PAD) + "," + (H - PAD);

    var text =
      "<div><p class='eyebrow'>EQUITY CURVE</p><h2>Net P&L, trade by trade</h2>" +
      "<p>Chronological, uncompressed. The line is the filled result at the TWS " +
      "terminal against the starting equity; the dashed line is starting equity.</p></div>" +
      "<svg viewBox='0 0 " + W + " " + H + "' class='curve-svg' role='img' " +
      "aria-label='Account equity curve ending at " + esc(usd(start + metrics.netPnl)) + "'>" +
      "<line x1='" + PAD + "' y1='" + yPos(start).toFixed(1) + "' x2='" + (W - PAD) + "' " +
      "y2='" + yPos(start).toFixed(1) + "' stroke='#26322e' stroke-dasharray='4 4'/>" +
      "<polygon points='" + area + "' fill='rgba(201,255,93,0.12)'/>" +
      "<polyline points='" + points + "' fill='none' stroke='#c9ff5d' stroke-width='3' " +
      "stroke-linejoin='round' stroke-linecap='round'/>";
    cumulative.forEach(function (value, index) {
      text += "<circle key='" + index + "' cx='" + xPos(index).toFixed(1) + "' " +
        "cy='" + yPos(start + value).toFixed(1) + "' r='3.5' fill='#101714' stroke='#c9ff5d' stroke-width='2'/>";
    });
    text += "<text x='" + (W - PAD) + "' y='" + (yPos(maxV) - 6).toFixed(1) + "' fill='#8f9995' " +
      "font-size='11' text-anchor='end'>" + esc(usd(maxV)) + "</text>" +
      "<text x='" + (W - PAD) + "' y='" + (yPos(minV) + 14).toFixed(1) + "' fill='#8f9995' " +
      "font-size='11' text-anchor='end'>" + esc(usd(minV)) + "</text></svg>";
    document.getElementById("curve-panel").innerHTML = text;
  }

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
    if (order.net_credit != null) bits.push("credit $" + order.net_credit);
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
    var html = "<div class='journal-plan'><small>MANAGEMENT PLAN</small>" +
      rows.map(function (row) {
        return "<span><b>" + esc(row[0]) + "</b>" + esc(row[1]) + "</span>";
      }).join("") + "</div>";
    return html;
  }

  function tradeCard(trade, accountEquity) {
    var won = trade.net_pnl > 0;
    var isLedger = trade.source === "ledger";
    var statusLabel = trade.status === "open" ? "OPEN" : won ? "CLOSED · WIN" : "CLOSED · LOSS";
    var statusClass = trade.status === "open" ? "open" : won ? "win" : "loss";
    var legs = trade.legs
      .map(function (leg) {
        return "<span class='" + esc(leg.action === "SELL" ? "sell" : "buy") + "'>" +
          esc(legLabel(leg)) + "</span>";
      })
      .join("");
    var tags = trade.tags
      .map(function (tag) { return "<span>#" + esc(tag) + "</span>"; })
      .join("");
    var links = trade.research
      .map(function (link) {
        return "<a href='" + esc(link.url) + "' target='_blank' rel='noreferrer'>" +
          esc(link.label) + " ↗</a>";
      })
      .join("");
    var strategyLine = strategyLabel(trade.strategy);
    if (trade.dte_at_entry) strategyLine += " · " + trade.dte_at_entry + " DTE at entry";
    strategyLine += " · IVR " + (trade.entry_ivr != null ? trade.entry_ivr : "—") + " at entry";
    if (trade.expected_move_pct != null) strategyLine += " · exp move \u00B1" + trade.expected_move_pct + "%";

    var riskPct = "";
    if (accountEquity > 0 && trade.capital_at_risk) {
      riskPct = " · " + ((trade.capital_at_risk / accountEquity) * 100).toFixed(1) + "% of account";
    }
    var rMultiple = "";
    if (trade.status === "closed" && trade.capital_at_risk > 0 && trade.capital_at_risk != null) {
      rMultiple = "R " + (trade.net_pnl / trade.capital_at_risk >= 0 ? "+" : "") +
        (trade.net_pnl / trade.capital_at_risk).toFixed(2);
    }

    return (
      "<article class='journal-card'>" +
      "<div class='journal-card-head'>" +
      "<div class='journal-symbol-row'><h3>" + esc(trade.symbol) + "</h3>" +
      "<span class='provenance-badge " + (isLedger ? "ledger" : "manual") + "'>" +
      (isLedger ? "TWS LEDGER" : "MANUAL") + "</span>" +
      "<span class='status-pill " + statusClass + "'>" + esc(statusLabel) + "</span></div>" +
      "<div class='journal-pnl " + (won ? "win" : "loss") + "'>" +
      "<small>NET P&amp;L</small><b>" + esc(usd(trade.net_pnl)) + "</b>" +
      "<span>" + esc(pct(trade.net_pnl_pct)) + " of max profit" +
      (rMultiple ? " · " + rMultiple : "") + "</span></div></div>" +
      "<p class='journal-strategy'>" + esc(strategyLine) + "</p>" +
      "<div class='journal-legs'>" + legs + "</div>" +
      "<div class='journal-meta'>" +
      "<span><small>OPENED</small><b>" + esc(dateLabel(trade.opened)) + "</b></span>" +
      "<span><small>" + (trade.status === "open" ? "CURRENT" : "CLOSED") + "</small><b>" +
      (trade.closed ? esc(dateLabel(trade.closed)) : "—") + "</b></span>" +
      "<span><small>CAPITAL AT RISK</small><b>" + esc(usd(trade.capital_at_risk).slice(1) + riskPct) + "</b></span>" +
      "<span><small>MAX PROFIT</small><b>" + esc(usd(trade.max_profit).slice(1)) + "</b></span></div>" +
      managementPlan(trade) +
      "<div class='journal-thesis'><small>THE WHY</small><p>" + esc(trade.reason) + "</p></div>" +
      "<div class='journal-exit'><small>WHAT HAPPENED</small><p>" + esc(trade.exit_note) + "</p></div>" +
      "<div class='journal-footer'>" +
      "<div class='journal-tags'>" + tags + "</div>" +
      "<div class='journal-links'>" + links + orderReceipt(trade) + "</div></div>" +
      "</article>"
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
    if (!keys.length) return;
    var blocks = keys.map(function (key) {
      var group = byMonth[key];
      var closed = group.filter(function (t) { return t.status === "closed"; });
      var winners = closed.filter(function (t) { return t.net_pnl > 0; });
      var net = group.reduce(function (s, t) { return s + t.net_pnl; }, 0);
      var winRate = closed.length ? (winners.length / closed.length) * 100 : null;
      var biggestWin = closed.length ? Math.max.apply(null, closed.map(function (t) { return t.net_pnl; })) : 0;
      var biggestLoss = closed.length ? Math.min.apply(null, closed.map(function (t) { return t.net_pnl; })) : 0;
      return "<div class='recap-card'><b>" + esc(monthLabel(key)) + "</b>" +
        "<span>" + group.length + " trades</span>" +
        "<span class='" + (net >= 0 ? "win" : "loss") + "'>" + esc(usd(net)) + " net</span>" +
        "<span>" + (winRate != null ? winRate.toFixed(0) + "% WR" : "no closed") + "</span>" +
        "<span class='win'>+" + esc(usd(Math.max(biggestWin, 0)).slice(1)) + "</span>" +
        "<span class='loss'>" + esc(usd(Math.min(biggestLoss, 0))) + "</span></div>";
    });
    document.getElementById("recaps").innerHTML =
      "<div class='journal-list-head'><div><p class='eyebrow'>BY MONTH</p>" +
      "<h2>Weeks and months, in review</h2></div></div><div class='recap-grid'>" + blocks.join("") + "</div>";
  }

  function render(data) {
    var trades = data.trades || [];
    var trader = data.trader || {};
    var metrics = computeMetrics(trades);
    var pf = Number.isFinite(metrics.profitFactor)
      ? metrics.profitFactor.toFixed(2)
      : metrics.profitFactor === Infinity ? "—" : "0.00";
    var streakValue = metrics.closed
      ? metrics.streak + " " + (metrics.lastClosedWon ? "wins" : "losses")
      : "—";
    var accountEquity = data.account_equity || 0;

    document.getElementById("metric-strip").innerHTML =
      metricCell("NET P&L", esc(usd(metrics.netPnl)), trades.length + " trades logged", metrics.netPnl >= 0 ? "win" : "loss") +
      metricCell("WIN RATE", metrics.winRate.toFixed(1) + "%", metrics.winners + " of " + metrics.closed + " closed") +
      metricCell("PROFIT FACTOR", pf, "gross wins / gross losses") +
      metricCell("EXPECTANCY", esc(usd(metrics.expectancy)), "avg per closed trade", metrics.expectancy >= 0 ? "win" : "loss") +
      metricCell("AVG WIN", esc(usd(metrics.avgWin)), "vs avg loss " + esc(usd(metrics.avgLoss)), "win") +
      metricCell("MAX DRAWDOWN", "-" + esc(usd(metrics.maxDrawdown).slice(1)), "peak-to-trough", "loss") +
      metricCell("DRAW DOWN / PEAK", "-" + esc(usd(metrics.drawdownFromPeak).slice(1)), "currently below peak", "loss") +
      metricCell("CURRENT STREAK", streakValue, "consecutive closed trades");

    renderCurve(metrics, accountEquity);
    renderRecaps(trades);

    document.getElementById("sort-note").textContent =
      "Newest first · " + trades.length + " entries";
    document.getElementById("journal-sub").textContent =
      (trader.tagline || "") + " — " + (trader.name || "") + " (" + (trader.handle || "") + ")" +
      " · journal updated " + (data.as_of || "") +
      ". Every placed trade appears here, winners and losers. Nothing is filtered.";

    if (data.verification && data.verification.ledger_sha) {
      document.getElementById("verify-note").textContent =
        "Every ledger entry above is derived from the TWS paper-order ledger " +
        "(sha " + data.verification.ledger_sha.slice(0, 12) + ") on " + (data.as_of || "") +
        " — recomputable, never edited by hand.";
    }

    var list = document.getElementById("journal-list");
    if (!trades.length) {
      list.innerHTML =
        "<div class='journal-empty'><b>No trades yet.</b><br/>" +
        "The first ThetaForge recommendation you place on TWS will appear here — " +
        "with the thesis, the structure, and the receipt.</div>";
      return;
    }
    var ordered = trades.slice().sort(function (a, b) { return a.opened.localeCompare(b.opened); });
    list.innerHTML = ordered.reverse()
      .map(function (trade) { return tradeCard(trade, accountEquity); })
      .join("");
  }

  function renderEmpty() {
    document.getElementById("metric-strip").innerHTML =
      "<div><small>NET P&amp;L</small><b>+$0</b><span>0 trades logged</span></div>" +
      "<div><small>WIN RATE</small><b>0.0%</b><span>0 of 0 closed</span></div>" +
      "<div><small>PROFIT FACTOR</small><b>—</b><span>gross wins / gross losses</span></div>" +
      "<div><small>EXPECTANCY</small><b>+$0</b><span>avg per closed trade</span></div>" +
      "<div><small>MAX DRAWDOWN</small><b>-$0</b><span>peak-to-trough</span></div>" +
      "<div><small>DRAW DOWN / PEAK</small><b>-$0</b><span>currently below peak</span></div>" +
      "<div><small>CURRENT STREAK</small><b>—</b><span>consecutive closed trades</span></div>";
    document.getElementById("journal-list").innerHTML =
      "<div class='journal-empty'><b>No trades yet.</b><br/>" +
      "The first ThetaForge recommendation you place on TWS will appear here — " +
      "with the thesis, the structure, and the receipt.</div>";
  }

  fetch("./trades.json")
    .then(function (response) { return response.json(); })
    .then(render)
    .catch(renderEmpty);
})();
