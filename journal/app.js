(function () {
  "use strict";

  var usd = function (value) {
    if (value == null || isNaN(value)) value = 0;
    return (value >= 0 ? "+" : "-") + "$" + Math.abs(value).toFixed(0);
  };
  var num = function (value, digits) {
    if (value == null || isNaN(value)) return "—";
    return value.toFixed(digits == null ? 1 : digits);
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
  var rMultiple = function (trade) {
    if (trade.status !== "closed") return null;
    var risk = Number(trade.capital_at_risk);
    if (!risk || risk <= 0) return null;
    return Number(trade.net_pnl) / risk;
  };

  var allTrades = [];
  var allTradesAccountEquity = 0;
  var engineFilter = "all";
  var engineOf = function (trade) {
    return trade.asset_class === "equity" ? "stocks" : "options";
  };
  var instrumentOf = function (trade) {
    if (trade.instrument_type === "etf" || trade.instrument_type === "stock") {
      return trade.instrument_type;
    }
    return trade.asset_class === "equity" ? "stock" : "option";
  };
  var instrumentLabel = { "option": "OPTIONS", "stock": "STOCKS", "etf": "ETFs" };

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
    var netPnl = trades.reduce(function (sum, trade) { return sum + (trade.net_pnl || 0); }, 0);
    var expectancy = closed.length ? netPnl / closed.length : 0;

    var ordered = trades.slice().sort(function (a, b) { return a.opened.localeCompare(b.opened); });
    var running = 0;
    var cumulative = ordered.map(function (trade) { running += trade.net_pnl || 0; return running; });
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
    var rValues = closed.map(rMultiple).filter(function (value) { return value != null; });
    var avgR = rValues.length
      ? rValues.reduce(function (sum, value) { return sum + value; }, 0) / rValues.length : 0;
    var best = closed.length ? closed.slice().sort(function (a, b) { return b.net_pnl - a.net_pnl; })[0] : null;
    var worst = closed.length ? closed.slice().sort(function (a, b) { return a.net_pnl - b.net_pnl; })[0] : null;

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
      avgR: avgR,
      best: best,
      worst: worst,
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
        "<div class='mix-card'>" +
        "<span class='mix-chip " + inst + "'>" + instrumentLabel[inst] + "</span>" +
        "<b>" + s.count + "</b><small>positions</small>" +
        "<span class='" + (s.net >= 0 ? "win" : "loss") + " mix-net'>" + esc(usd(s.net)) + "</span>" +
        "<small>net P&amp;L" + (s.winRate != null ? " · " + s.winRate.toFixed(0) + "% WR (" + s.wins + "/" + s.closed + ")" : " · no closed trades") + "</small>" +
        (inst === "etf" || inst === "stock"
          ? "<small>" + equity.toLocaleString() + " shares traded across the book</small>" : "") +
        "</div>"
      );
    });
    document.getElementById("asset-mix").innerHTML =
      "<div class='journal-list-head'><div><p class='eyebrow'>ASSET MIX</p>" +
      "<h2>Options, stocks, ETFs — where the P&L came from</h2></div></div>" +
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
    if (!keys.length) {
      el.innerHTML = "<div class='chart-empty'>No closed trades yet — monthly P&L appears here as positions close.</div>";
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
      "' stroke='#3a4a43' stroke-width='1.5'/>";
    html += "<text x='" + (PAD_L - 8) + "' y='" + (zero - 3).toFixed(1) + "' fill='#8f9995' font-size='10' text-anchor='end'>0</text>";
    [1, -1].forEach(function (sign) {
      var y = zero - sign * (ih / 2 - 4);
      html += "<line x1='" + PAD_L + "' y1='" + y.toFixed(1) + "' x2='" + (W - PAD_R) + "' y2='" + y.toFixed(1) +
        "' stroke='#1d2923'/>";
      html += "<text x='" + (PAD_L - 8) + "' y='" + (y - 3).toFixed(1) + "' fill='#8f9995' font-size='10' text-anchor='end'>" +
        esc(usd(sign * maxAbs)) + "</text>";
    });
    keys.forEach(function (key, i) {
      var v = byMonth[key].net;
      var x = PAD_L + i * step + (step - barW) / 2;
      var y = v >= 0 ? yFor(v) : zero;
      var h = Math.max(Math.abs(v) / maxAbs * (ih / 2 - 4), v === 0 ? 0 : 2);
      var fill = v >= 0 ? "#c9ff5d" : "#f0a79b";
      html += "<rect x='" + x.toFixed(1) + "' y='" + y.toFixed(1) + "' width='" + barW.toFixed(1) +
        "' height='" + h.toFixed(1) + "' fill='" + fill + "' rx='2'>" +
        "<title>" + esc(monthLabel(key)) + " · " + esc(usd(v)) +
        " (" + byMonth[key].closed + " closed)</title></rect>";
      if (keys.length <= 18) {
        html += "<text x='" + (x + barW / 2).toFixed(1) + "' y='" + (y - 5).toFixed(1) +
          "' fill='" + (v >= 0 ? "#c9ff5d" : "#f0a79b") + "' font-size='10' text-anchor='middle' font-weight='bold'>" +
          esc(usd(v)) + "</text>";
      }
      var label = monthLabel(key).split(" ");
      html += "<text x='" + (PAD_L + i * step + step / 2).toFixed(1) + "' y='" + (H - PAD_B + 16).toFixed(1) +
        "' fill='#8f9995' font-size='10' text-anchor='middle'>" + esc(label[0] + " " + label[1].slice(2)) + "</text>";
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
        "<div class='hbar-row'><div class='hbar-label'>" + esc(strategyLabel(row.key)) +
        "<small>" + row.closed + " closed · " + row.count + " total" +
        (row.winRate != null ? " · " + row.winRate.toFixed(0) + "% WR" : "") + "</small></div>" +
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
      var buyCell = row.avgBuy != null ? "$" + row.avgBuy.toFixed(2) : "—";
      var sellCell = row.avgSell != null ? "$" + row.avgSell.toFixed(2) : "—";
      return (
        "<tr><td><b>" + esc(row.key) + "</b></td>" +
        "<td><span class='mix-chip " + row.instrument + "'>" + instrumentLabel[row.instrument] + "</span></td>" +
        "<td>" + row.count + "</td>" +
        "<td>" + (row.winRate != null ? row.winRate.toFixed(0) + "%" : "—") + "</td>" +
        "<td>" + buyCell + "</td><td>" + sellCell + "</td>" +
        "<td class='net-cell'><div class='hbar-track'><div class='hbar " +
        (row.net >= 0 ? "win" : "loss") + "' style='width:" + width.toFixed(1) + "%'></div></div>" +
        "<b class='" + (row.net >= 0 ? "win" : "loss") + "'>" + esc(usd(row.net)) + "</b></td></tr>"
      );
    }).join("");
    var empty = rows.length ? "" : "<div class='chart-empty'>No trades yet.</div>";
    document.getElementById("analytics-symbol").innerHTML =
      "<div class='journal-list-head'><div><p class='eyebrow'>BY SYMBOL</p>" +
      "<h2>Profit per name — with average buy/sell prices</h2></div></div>" + empty +
      "<table class='symbol-table'><thead><tr>" +
      "<th>SYMBOL</th><th>TYPE</th><th>TRADES</th><th>WIN RATE</th>" +
      "<th>AVG BUY</th><th>AVG SELL</th><th>NET P&amp;L</th></tr></thead><tbody>" + body + "</tbody></table>";
  }

  function renderExtremes(metrics) {
    var card = function (trade, cls, title) {
      if (!trade) {
        return "<div class='extreme-card " + cls + "'><small>" + title + "</small>" +
          "<b>—</b><span>No closed trades yet</span></div>";
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
    var html = "<div class='journal-plan'><small>MANAGEMENT PLAN</small>" +
      rows.map(function (row) {
        return "<span><b>" + esc(row[0]) + "</b>" + esc(row[1]) + "</span>";
      }).join("") + "</div>";
    return html;
  }

  function tradeCard(trade, accountEquity) {
    var won = trade.net_pnl > 0;
    var isLedger = trade.source === "ledger";
    var isEquity = trade.asset_class === "equity";
    var statusLabel = trade.status === "open" ? "OPEN" : won ? "CLOSED · WIN" : "CLOSED · LOSS";
    var statusClass = trade.status === "open" ? "open" : won ? "win" : "loss";
    var inst = instrumentOf(trade);
    var prices = tradePrices(trade);

    var legs;
    var strategyLine;
    if (isEquity) {
      var shares = trade.order && trade.order.quantity != null ? trade.order.quantity : (trade.shares || "");
      var entry = trade.entry_price != null ? "@ " + trade.entry_price : "";
      var stop = trade.stop_price != null ? "stop " + trade.stop_price : "";
      var target = trade.target_price != null ? "target " + trade.target_price : "";
      var sold = prices.sell != null ? " · sold @ " + prices.sell : "";
      legs = "<span class='buy'>BUY " + esc(shares) + " shares " + esc(entry) +
        (stop ? " · " + esc(stop) : "") +
        (target ? " · " + esc(target) : "") + "</span>" +
        (sold ? "<span class='sell'>SELL " + esc(shares) + " shares " + esc(sold) + "</span>" : "");
      strategyLine = "Long " + esc(trade.symbol) + " · momentum/trend long" +
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
      strategyLine += " · IVR " + (trade.entry_ivr != null ? trade.entry_ivr : "—") + " at entry";
      if (trade.expected_move_pct != null) strategyLine += " · exp move \u00B1" + trade.expected_move_pct + "%";
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
    var rText = r != null ? " · " + (r >= 0 ? "+" : "") + r.toFixed(2) + "R" : "";
    var resultSub = isEquity
      ? (r != null ? "realized " + rText + " on risk" : "open · risk defined by stop")
      : esc(pct(trade.net_pnl_pct)) + " of max profit";

    var secondMeta;
    if (isEquity) {
      secondMeta = "<span><small>TARGET</small><b>" +
        esc(trade.target_price != null ? "$" + trade.target_price : "—") + "</b></span>";
    } else {
      secondMeta = "<span><small>MAX PROFIT</small><b>" + esc(usd(trade.max_profit).slice(1)) + "</b></span>";
    }

    return (
      "<article class='journal-card'>" +
      "<div class='journal-card-head'>" +
      "<div class='journal-symbol-row'><h3>" + esc(trade.symbol) + "</h3>" +
      "<span class='engine-chip " + inst + "'>" + instrumentLabel[inst] + "</span>" +
      "<span class='provenance-badge " + (isLedger ? "ledger" : "manual") + "'>" +
      (isLedger ? "TWS LEDGER" : "MANUAL") + "</span>" +
      "<span class='status-pill " + statusClass + "'>" + esc(statusLabel) + "</span></div>" +
      "<div class='journal-pnl " + (won ? "win" : "loss") + "'>" +
      "<small>NET P&amp;L</small><b>" + esc(usd(trade.net_pnl)) + "</b>" +
      "<span>" + (trade.status === "open" ? "open position" : resultSub) + "</span></div></div>" +
      "<p class='journal-strategy'>" + esc(strategyLine) + "</p>" +
      "<div class='journal-legs'>" + legs + "</div>" +
      "<div class='journal-meta'>" +
      "<span><small>OPENED</small><b>" + esc(dateLabel(trade.opened)) + "</b></span>" +
      "<span><small>" + (trade.status === "open" ? "CURRENT" : "CLOSED") + "</small><b>" +
      (trade.closed ? esc(dateLabel(trade.closed)) : "—") + "</b></span>" +
      "<span><small>CAPITAL AT RISK</small><b>" + esc(usd(trade.capital_at_risk).slice(1) + riskPct) + "</b></span>" +
      secondMeta + "</div>" +
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
      "<div class='journal-list-head'><div><p class='eyebrow'>BY MONTH — DETAIL</p>" +
      "<h2>Weeks and months, in review</h2></div></div><div class='recap-grid'>" + blocks.join("") + "</div>";
  }

  function wireFilters() {
    var bar = document.getElementById("journal-filters");
    bar.addEventListener("click", function (event) {
      var button = event.target.closest("[data-filter]");
      if (!button) return;
      engineFilter = button.getAttribute("data-filter");
      Array.prototype.forEach.call(bar.querySelectorAll("[data-filter]"), function (tab) {
        tab.setAttribute("aria-selected", tab === button ? "true" : "false");
      });
      renderList();
    });
  }

  function renderList() {
    var list = document.getElementById("journal-list");
    var filtered = allTrades;
    if (engineFilter !== "all") {
      filtered = allTrades.filter(function (trade) { return engineOf(trade) === engineFilter; });
    }
    if (!filtered.length) {
      var engineName = engineFilter === "stocks" ? "Stocks" : engineFilter === "options" ? "Options" : "All";
      list.innerHTML =
        "<div class='journal-empty'><b>No " + engineName.toLowerCase() + " trades yet.</b><br/>" +
        "The first " + engineName + " recommendation you place on paper will appear here, " +
        "with the thesis, the structure, and the receipt.</div>";
      return;
    }
    var ordered = filtered.slice().sort(function (a, b) { return a.opened.localeCompare(b.opened); });
    list.innerHTML = ordered.reverse()
      .map(function (trade) { return tradeCard(trade, allTradesAccountEquity); })
      .join("");
  }

  function render(data) {
    var trades = data.trades || [];
    allTrades = trades;
    allTradesAccountEquity = data.account_equity || 0;
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
      metricCell("AVG R", (metrics.avgR >= 0 ? "+" : "") + metrics.avgR.toFixed(2), "mean R across closed trades", metrics.avgR >= 0 ? "win" : "loss") +
      metricCell("MAX DRAWDOWN", "-" + esc(usd(metrics.maxDrawdown).slice(1)), "peak-to-trough", "loss") +
      metricCell("DRAW DOWN / PEAK", "-" + esc(usd(metrics.drawdownFromPeak).slice(1)), "currently below peak", "loss");

    renderCurve(metrics, accountEquity);
    renderAssetMix(trades);
    renderMonthlyChart(trades);
    renderStrategy(trades);
    renderSymbol(trades);
    renderExtremes(metrics);
    renderRecaps(trades);

    var optionsCount = trades.filter(function (trade) { return engineOf(trade) === "options"; }).length;
    var stocksCount = trades.filter(function (trade) { return engineOf(trade) === "stocks"; }).length;
    document.getElementById("sort-note").textContent =
      "Newest first · " + trades.length + " entries · " +
      optionsCount + " options / " + stocksCount + " stocks";
    document.getElementById("journal-sub").textContent =
      (trader.tagline || "") + " · " + (trader.name || "") + " (" + (trader.handle || "") + ")" +
      " · journal updated " + (data.as_of || "") +
      ". Every placed trade appears here, winners and losers. Nothing is filtered.";

    if (data.verification && data.verification.ledger_sha) {
      document.getElementById("verify-note").textContent =
        "Every ledger entry above is derived from the TWS paper-order ledger " +
        "(sha " + data.verification.ledger_sha.slice(0, 12) + ") on " + (data.as_of || "") +
        ". Recomputable, never edited by hand.";
    }

    renderList();
    wireFilters();
  }

  function renderEmpty() {
    document.getElementById("metric-strip").innerHTML =
      "<div><small>NET P&amp;L</small><b>+$0</b><span>0 trades logged</span></div>" +
      "<div><small>WIN RATE</small><b>0.0%</b><span>0 of 0 closed</span></div>" +
      "<div><small>PROFIT FACTOR</small><b>—</b><span>gross wins / gross losses</span></div>" +
      "<div><small>EXPECTANCY</small><b>+$0</b><span>avg per closed trade</span></div>" +
      "<div><small>AVG R</small><b>+0.00</b><span>mean R across closed trades</span></div>" +
      "<div><small>MAX DRAWDOWN</small><b>-$0</b><span>peak-to-trough</span></div>" +
      "<div><small>DRAW DOWN / PEAK</small><b>-$0</b><span>currently below peak</span></div>" +
      "<div><small>CURRENT STREAK</small><b>—</b><span>consecutive closed trades</span></div>";
    ["asset-mix", "monthly-chart", "analytics-strategy", "analytics-symbol", "analytics-extremes"]
      .forEach(function (id) {
        document.getElementById(id).innerHTML =
          "<div class='chart-empty'>No trades yet — the KPI breakdowns appear here as positions close.</div>";
      });
    document.getElementById("journal-list").innerHTML =
      "<div class='journal-empty'><b>No trades yet.</b><br/>" +
      "The first ThetaForge recommendation you place on TWS will appear here, " +
      "with the thesis, the structure, and the receipt.</div>";
  }

  fetch("./trades.json")
    .then(function (response) { return response.json(); })
    .then(render)
    .catch(renderEmpty);
})();
