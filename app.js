/* ThetaForge public trade journal.
 *
 * One state object drives every view: the bucket switcher scopes everything,
 * a period (month/week) scopes the review panel and the trade table, and the
 * table filters narrow the list further. Rendering is plain string templates
 * into a handful of mount points; charts are tiny inline SVGs whose colors
 * come from CSS custom properties, so light/dark mode needs no JS.
 */
(function () {
  "use strict";

  /* ---- constants -------------------------------------------------------- */

  const LIVE_REFETCH_MS = 300000; // trades.json is re-pulled every 5 minutes so
                                  // an hourly VM snapshot never sits stale
                                  // longer than that on an open tab.
  const PAGE = 40;
  const DEFAULT_BUCKET = "algo_trader";
  const BUCKET_META = {
    algo_trader: {
      label: "Algo Trader",
      blurb: "The original book: every trade the options and stock/ETF engines have placed on the paper account since day one.",
    },
    infinity_1k: {
      label: "$1000 to Infinity",
      blurb: "A separate $1,000 bucket that compounds from a standing start. Account value is $1,000 plus everything it has realized, with open marks shown on top.",
      start: 1000,
    },
    all: { label: "All buckets", blurb: "Both buckets together, measured against their combined starting capital." },
  };
  const INST_LABEL = { option: "Options", stock: "Stocks", etf: "ETFs" };
  const INST_SHORT = { option: "OPT", stock: "STK", etf: "ETF" };
  const STRATEGY_LABEL = {
    etf_rotation: "ETF Rotation",
    equity_momentum: "Equity Momentum",
    bull_put: "Bull Put Spread",
    bull_put_credit: "Bull Put Credit Spread",
    bear_call: "Bear Call Spread",
    bear_call_credit: "Bear Call Credit Spread",
    iron_condor: "Iron Condor",
    cash_secured_put: "Cash-Secured Put",
    call_debit: "Call Debit Spread",
    call_debit_spread: "Call Debit Spread",
    put_debit: "Put Debit Spread",
    put_debit_spread: "Put Debit Spread",
  };
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

  /* ---- small helpers ---------------------------------------------------- */

  const $ = (id) => document.getElementById(id);
  const esc = (value) =>
    String(value == null ? "" : value).replace(/[&<>"']/g, (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const isNum = (v) => v != null && v !== "" && isFinite(Number(v));
  const plural = (n, word, many) => n + " " + (n === 1 ? word : (many || word + "s"));

  // Money: whole dollars from $100 up, cents below (the $1,000 bucket lives
  // in single and double digits, where rounding would hide the result).
  function money(value, signed) {
    const v = Number(value) || 0;
    const abs = Math.abs(v);
    const body = abs >= 100
      ? Math.round(abs).toLocaleString("en-US")
      : abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (signed === false) return (v < 0 ? "-$" : "$") + body;
    return (v > 0 ? "+$" : v < 0 ? "-$" : "$") + body;
  }
  const plain = (value) => money(Math.abs(Number(value) || 0), false);
  const price = (value, digits) => (isNum(value) ? "$" + Number(value).toFixed(digits == null ? 2 : digits) : "n/a");
  const pctText = (value, digits) => {
    if (!isNum(value)) return "n/a";
    const n = Number(value);
    return (n > 0 ? "+" : "") + n.toFixed(digits == null ? 1 : digits) + "%";
  };
  const rText = (r) => (r == null ? "–" : (r > 0 ? "+" : "") + r.toFixed(2) + "R");
  const tone = (v) => (v > 0 ? "gain" : v < 0 ? "loss" : "flat");
  const strategyLabel = (key) => {
    const raw = String(key == null ? "" : key);
    return STRATEGY_LABEL[raw] || raw.replace(/_/g, " ").replace(/\b[a-z]/g, (c) => c.toUpperCase()) || "Unknown";
  };

  // Dates are plain YYYY-MM-DD strings; parse them as UTC so the browser's
  // timezone never shifts a trade into a neighbouring day or week.
  const ymd = (s) => String(s || "").slice(0, 10);
  const toUTC = (s) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ""));
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3]) : NaN;
  };
  const fromUTC = (t) => new Date(t).toISOString().slice(0, 10);
  const shortDate = (s) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ""));
    return m ? MONTHS[+m[2] - 1] + " " + +m[3] : "n/a";
  };
  const longDate = (s) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ""));
    return m ? MONTHS[+m[2] - 1] + " " + +m[3] + ", " + m[1] : "n/a";
  };
  const weekStart = (s) => {
    const t = toUTC(s);
    if (!isFinite(t)) return "";
    const dow = (new Date(t).getUTCDay() + 6) % 7; // Monday = 0
    return fromUTC(t - dow * 86400000);
  };
  const utcTime = (iso) => {
    const m = String(iso || "").match(/T(\d{2}):(\d{2})/);
    return m ? m[1] + ":" + m[2] + " UTC" : "";
  };

  /* ---- data model ------------------------------------------------------- */

  let DATA = null;
  let ALL = [];          // normalized trades, every bucket
  let BUCKETS = {};      // bucket key -> { label, blurb, start }
  let lastPayload = null;

  function instrumentOf(t) {
    if (t.instrument_type === "etf" || t.instrument_type === "stock") return t.instrument_type;
    return t.asset_class === "equity" ? "stock" : "option";
  }

  function liveMark(t) {
    const live = t.live;
    if (!live || !isNum(live.price) || !isNum(live.pnl)) return null;
    return live;
  }

  function normalize(t) {
    const closed = t.status === "closed";
    const mark = closed ? null : liveMark(t);
    const risk = Number(t.capital_at_risk) || 0;
    const realized = closed ? Number(t.net_pnl) || 0 : 0;
    const unrealized = mark ? Number(mark.pnl) : null;
    const pnl = closed ? realized : unrealized;
    const order = t.order || {};
    const opened = ymd(t.opened || order.submitted_at || t.timestamp);
    const closedOn = closed ? ymd(t.closed || (t.close_order && t.close_order.updated_at) || t.timestamp) : "";
    const holdDays = closed && isFinite(toUTC(opened)) && isFinite(toUTC(closedOn))
      ? Math.max(0, (toUTC(closedOn) - toUTC(opened)) / 86400000) : null;
    return Object.assign({}, t, {
      _bucket: t.bucket === "infinity_1k" ? "infinity_1k" : (t.bucket && BUCKET_META[t.bucket] && t.bucket !== "all" ? t.bucket : "algo_trader"),
      _inst: instrumentOf(t),
      _closed: closed,
      _opened: opened,
      _closedOn: closedOn,
      _date: closed ? closedOn : opened,            // the day the trade is filed under
      _stamp: String(order.submitted_at || order.updated_at || t.timestamp || opened),
      _risk: risk,
      _realized: realized,
      _unrealized: unrealized,
      _pnl: pnl,                                    // realized, or latest mark, or null
      _r: pnl != null && risk > 0 ? pnl / risk : null,
      _won: closed && realized > 0,
      _mark: mark,
      _hold: holdDays,
      _search: (String(t.symbol || "") + " " + strategyLabel(t.strategy) + " " + (t.strategy || "")).toLowerCase(),
    });
  }

  function buildBuckets(data) {
    const extra = (data && data.buckets) || {};
    const out = {};
    ["algo_trader", "infinity_1k"].forEach((key) => {
      const meta = BUCKET_META[key];
      const cfg = extra[key] || {};
      let start = isNum(cfg.starting_capital) ? Number(cfg.starting_capital) : meta.start;
      if (start == null) start = Number(data.account_equity) || 0;
      out[key] = { key, label: cfg.label || meta.label, blurb: meta.blurb, start };
    });
    out.all = { key: "all", label: BUCKET_META.all.label, blurb: BUCKET_META.all.blurb,
      start: out.algo_trader.start + out.infinity_1k.start };
    return out;
  }

  function metrics(trades) {
    const closed = trades.filter((t) => t._closed).sort((a, b) => a._closedOn.localeCompare(b._closedOn) || a._stamp.localeCompare(b._stamp));
    const open = trades.filter((t) => !t._closed);
    const wins = closed.filter((t) => t._realized > 0);
    const losses = closed.filter((t) => t._realized <= 0);
    const grossWin = wins.reduce((s, t) => s + t._realized, 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + t._realized, 0));
    const net = grossWin - grossLoss;
    const marked = open.filter((t) => t._mark);
    const unrealized = marked.reduce((s, t) => s + t._unrealized, 0);
    let run = 0, peak = 0, dd = 0;
    closed.forEach((t) => { run += t._realized; peak = Math.max(peak, run); dd = Math.max(dd, peak - run); });
    const holds = closed.map((t) => t._hold).filter((d) => d != null);
    const rs = closed.map((t) => t._r).filter((r) => r != null);
    const byPnl = closed.slice().sort((a, b) => b._realized - a._realized);
    return {
      count: trades.length,
      closed,
      open,
      wins: wins.length,
      losses: losses.length,
      grossWin,
      grossLoss,
      net,
      winRate: closed.length ? (wins.length / closed.length) * 100 : null,
      profitFactor: closed.length ? (grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0) : null,
      avgWin: wins.length ? grossWin / wins.length : null,
      avgLoss: losses.length ? grossLoss / losses.length : null,
      expectancy: closed.length ? net / closed.length : null,
      maxDrawdown: closed.length ? dd : null,
      avgHold: holds.length ? holds.reduce((s, d) => s + d, 0) / holds.length : null,
      avgR: rs.length ? rs.reduce((s, r) => s + r, 0) / rs.length : null,
      best: byPnl.length && byPnl[0]._realized > 0 ? byPnl[0] : null,
      worst: byPnl.length && byPnl[byPnl.length - 1]._realized <= 0 ? byPnl[byPnl.length - 1] : null,
      marked: marked.length,
      unrealized,
      riskOpen: open.reduce((s, t) => s + t._risk, 0),
      lastMarkAt: marked.reduce((m, t) => (String(t._mark.at || "") > m ? String(t._mark.at) : m), ""),
    };
  }

  const pfText = (pf) => (pf == null ? "n/a" : pf === Infinity ? "∞" : pf.toFixed(2));

  /* ---- state + URL ------------------------------------------------------ */

  const S = {
    bucket: DEFAULT_BUCKET,
    grain: "month",
    period: "",        // "" = all time, else "2026-10" (month) or "2026-10-26" (week start)
    q: "",
    status: "all",
    asset: "all",
    strategy: "all",
    from: "",
    to: "",
    group: "none",
    sortKey: "date",
    sortDir: -1,
    limit: PAGE,
    trade: "",         // id of the trade open in the drawer
  };

  function readHash() {
    const p = new URLSearchParams(location.hash.replace(/^#/, ""));
    if (["algo_trader", "infinity_1k", "all"].includes(p.get("b"))) S.bucket = p.get("b");
    if (p.get("g") === "week" || p.get("g") === "month") S.grain = p.get("g");
    if (p.get("p")) S.period = p.get("p");
    if (p.get("t")) S.trade = p.get("t");
  }

  function writeHash() {
    const p = new URLSearchParams();
    if (S.bucket !== DEFAULT_BUCKET) p.set("b", S.bucket);
    if (S.grain !== "month") p.set("g", S.grain);
    if (S.period) p.set("p", S.period);
    if (S.trade) p.set("t", S.trade);
    const next = p.toString() ? "#" + p.toString() : location.pathname + location.search;
    try { history.replaceState(null, "", next); } catch (e) { /* file:// or sandboxed */ }
  }

  /* ---- scoping ---------------------------------------------------------- */

  const inBucket = (t) => S.bucket === "all" || t._bucket === S.bucket;
  const periodOf = (t, grain) => (grain === "week" ? weekStart(t._date) : t._date.slice(0, 7));
  const inPeriod = (t) => !S.period || periodOf(t, S.grain) === S.period;
  const bucketTrades = () => ALL.filter(inBucket);
  const scopeTrades = () => ALL.filter((t) => inBucket(t) && inPeriod(t));

  function periodLabel(key, grain, long) {
    if (!key) return "All time";
    if (grain === "week") {
      const end = fromUTC(toUTC(key) + 4 * 86400000);
      if (long) return "Week of " + longDate(key);
      return shortDate(key) + "–" + (key.slice(5, 7) === end.slice(5, 7) ? String(+end.slice(8, 10)) : shortDate(end));
    }
    const [y, m] = key.split("-");
    return (long ? MONTHS_LONG : MONTHS)[+m - 1] + " " + y;
  }

  function listTrades() {
    const q = S.q.trim().toLowerCase();
    let out = scopeTrades().filter((t) => {
      if (q && !t._search.includes(q)) return false;
      if (S.asset !== "all" && t._inst !== S.asset) return false;
      if (S.strategy !== "all" && t.strategy !== S.strategy) return false;
      if (S.status === "open" && t._closed) return false;
      if (S.status === "closed" && !t._closed) return false;
      if (S.status === "win" && !(t._closed && t._realized > 0)) return false;
      if (S.status === "loss" && !(t._closed && t._realized <= 0)) return false;
      if (S.from && t._opened < S.from && (t._closedOn || t._opened) < S.from) return false;
      if (S.to && t._opened > S.to) return false;
      return true;
    });
    const dir = S.sortDir;
    const statusRank = (t) => (!t._closed ? 0 : t._realized > 0 ? 1 : 2);
    const keyFn = {
      date: (t) => t._stamp,
      symbol: (t) => String(t.symbol || ""),
      strategy: (t) => strategyLabel(t.strategy),
      status: statusRank,
      risk: (t) => t._risk,
      r: (t) => (t._r == null ? -Infinity : t._r),
      pnl: (t) => (t._pnl == null ? -Infinity : t._pnl),
    }[S.sortKey] || ((t) => t._stamp);
    out.sort((a, b) => {
      const x = keyFn(a), y = keyFn(b);
      const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return c * dir || b._stamp.localeCompare(a._stamp);
    });
    return out;
  }

  /* ---- chart helpers ---------------------------------------------------- */

  const CHARTS = {};
  let chartSeq = 0;

  /* Line chart over evenly spaced points (exit by exit). Colors come from CSS
     classes so the same SVG reads correctly in light and dark mode.
     pts: [{ y, tip, date }], opts: { base, tail: {y, tip}, label, fmt } */
  function lineChart(pts, opts) {
    const id = "c" + ++chartSeq;
    const W = 1000, H = 200, T = 16, B = 26;
    const all = pts.map((p) => p.y).concat(opts.base != null ? [opts.base] : []).concat(opts.tail ? [opts.tail.y] : []);
    let lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
    const padV = (hi - lo) * 0.08;
    lo -= padV; hi += padV;
    const n = pts.length + (opts.tail ? 1 : 0);
    const x = (i) => (n <= 1 ? 0.5 : i / (n - 1)) * W;
    const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    const coords = pts.map((p, i) => [x(i), y(p.y)]);
    const f = (v) => v.toFixed(1);
    const line = coords.map((c) => f(c[0]) + "," + f(c[1])).join(" ");
    const baseY = opts.base != null ? y(opts.base) : null;
    const last = pts[pts.length - 1];
    const fmt = opts.fmt || ((v) => money(v, false));
    const pctX = (v) => ((v / W) * 100).toFixed(2) + "%";
    const pctY = (v) => ((v / H) * 100).toFixed(2) + "%";
    let svg = "<svg viewBox='0 0 " + W + " " + H + "' preserveAspectRatio='none' aria-hidden='true' focusable='false'>";
    let over = "";
    [hi - padV, lo + padV].forEach((v) => {
      svg += "<line class='grid' x1='0' x2='" + W + "' y1='" + f(y(v)) + "' y2='" + f(y(v)) + "'/>";
      over += "<span class='axis y' style='top:" + pctY(y(v)) + "'>" + esc(fmt(v)) + "</span>";
    });
    if (baseY != null) svg += "<line class='base' x1='0' x2='" + W + "' y1='" + f(baseY) + "' y2='" + f(baseY) + "'/>";
    if (coords.length > 1 && baseY != null) {
      svg += "<path class='area " + tone(last.y - (opts.base || 0)) + "' d='M" + f(coords[0][0]) + "," + f(baseY) + " L" +
        line.replace(/ /g, " L") + " L" + f(coords[coords.length - 1][0]) + "," + f(baseY) + "Z'/>";
    }
    if (coords.length > 1) svg += "<polyline class='line' points='" + line + "'/>";
    const lc = coords[coords.length - 1];
    if (opts.tail) {
      const tx = x(n - 1), ty = y(opts.tail.y);
      svg += "<line class='tail' x1='" + f(lc[0]) + "' y1='" + f(lc[1]) + "' x2='" + f(tx) + "' y2='" + f(ty) + "'/>";
      over += "<i class='dot tail-dot' style='left:" + pctX(tx) + ";top:" + pctY(ty) + "'></i>";
    }
    over += "<i class='dot end-dot' style='left:" + pctX(lc[0]) + ";top:" + pctY(lc[1]) + "'></i>";
    if (pts.length && pts[0].date) over += "<span class='axis x first'>" + esc(shortDate(pts[0].date)) + "</span>";
    const endDate = opts.tail ? "now" : last.date ? shortDate(last.date) : "";
    if (endDate && n > 1) over += "<span class='axis x last'>" + esc(endDate) + "</span>";
    svg += "<line class='cross' y1='" + T + "' y2='" + (H - B) + "' x1='0' x2='0' visibility='hidden'/></svg>";
    over += "<i class='dot hover-dot' hidden></i>";
    const hover = coords.map((c, i) => ({ x: c[0], y: c[1], tip: pts[i].tip }));
    if (opts.tail) hover.push({ x: x(n - 1), y: y(opts.tail.y), tip: opts.tail.tip });
    CHARTS[id] = { W, H, hover };
    return "<div class='chart' data-chart='" + id + "' role='img' aria-label='" + esc(opts.label) + "'>" +
      "<div class='plot'>" + svg + over + "</div></div>";
  }

  function hbars(rows, attr) {
    if (!rows.length) return "<p class='empty-note'>Nothing here yet.</p>";
    const max = Math.max.apply(null, rows.filter((r) => r.closed).map((r) => Math.abs(r.value)).concat([1e-9]));
    return "<ul class='hbars'>" + rows.map((r) => {
      const w = r.closed ? Math.max(1.5, (Math.abs(r.value) / max) * 100) : 0;
      return "<li><button type='button' class='hbar-row' " + attr + "='" + esc(r.key) + "' title='Show these trades'>" +
        "<span class='hbar-label'>" + esc(r.label) + "<small>" + esc(r.sub) + "</small></span>" +
        "<span class='hbar-track' aria-hidden='true'><span class='hbar " + tone(r.value) + "' style='width:" + w.toFixed(1) + "%'></span></span>" +
        (r.closed ? "<b class='hbar-value " + tone(r.value) + "'>" + esc(money(r.value)) + "</b>" : "<b class='hbar-value flat' title='Nothing closed yet'>open</b>") + "</button></li>";
    }).join("") + "</ul>";
  }

  function groupBreakdown(trades, keyOf, labelOf) {
    const groups = {};
    trades.forEach((t) => {
      const k = keyOf(t);
      (groups[k] = groups[k] || []).push(t);
    });
    return Object.keys(groups).map((k) => {
      const m = metrics(groups[k]);
      const sub = [plural(m.count, "trade")];
      if (m.closed.length) sub.push(m.winRate.toFixed(0) + "% win");
      if (m.open.length) sub.push(m.open.length + " open" + (m.marked ? " (" + money(m.unrealized) + " marked)" : ""));
      return { key: k, label: labelOf(k), value: m.net, sub: sub.join(" · "), closed: m.closed.length };
    }).sort((a, b) => b.value - a.value || b.closed - a.closed);
  }

  /* ---- the book (bucket header) ----------------------------------------- */

  function accountSeries(trades, start) {
    const m = metrics(trades);
    let run = start;
    const pts = [{ y: start, date: m.closed.length ? m.closed[0]._closedOn : "", tip: "<b>Starting capital</b><span>" + esc(money(start, false)) + "</span>" }];
    m.closed.forEach((t) => {
      run += t._realized;
      pts.push({
        y: run,
        date: t._closedOn,
        tip: "<b>" + esc(shortDate(t._closedOn)) + " · " + esc(t.symbol) + "</b>" +
          "<span class='" + tone(t._realized) + "'>this exit " + esc(money(t._realized)) + "</span>" +
          "<span>account " + esc(money(run, false)) + "</span>",
      });
    });
    const tail = m.marked
      ? { y: run + m.unrealized, date: "now",
          tip: "<b>Now, with open marks</b><span class='" + tone(m.unrealized) + "'>" + esc(money(m.unrealized)) + " unrealized on " + plural(m.marked, "position") + "</span><span>account " + esc(money(run + m.unrealized, false)) + "</span>" }
      : null;
    return { pts, tail, m };
  }

  function kpi(label, value, sub, cls, extra) {
    return "<div class='kpi" + (extra ? " " + extra : "") + "'><small>" + esc(label) + "</small><b class='" + (cls || "") + "'>" + value + "</b>" +
      (sub ? "<span>" + sub + "</span>" : "") + "</div>";
  }

  function renderBook() {
    const b = BUCKETS[S.bucket];
    const trades = bucketTrades();
    const el = $("book");
    const head = "<div class='section-head'><div><p class='eyebrow'>STATE OF THE BOOK</p>" +
      "<h2 id='book-title'>" + esc(b.label) + "</h2><p class='section-note'>" + esc(b.blurb) + "</p></div>" +
      (DATA && DATA.as_of ? "<span class='result-note'>updated " + esc(longDate(DATA.as_of)) + "</span>" : "") + "</div>";

    if (!trades.length) {
      el.innerHTML = head + emptyBucket(b);
      return;
    }
    const s = accountSeries(trades, b.start);
    const m = s.m;
    const value = b.start + m.net + m.unrealized;
    const markNote = m.marked ? " · marked " + esc(utcTime(m.lastMarkAt) || "") : "";
    const tiles =
      kpi("ACCOUNT VALUE", esc(money(value, false)),
        "started at " + esc(money(b.start, false)) + " · " + esc(pctText(b.start ? ((value - b.start) / b.start) * 100 : 0, 2)), tone(value - b.start), "hero") +
      kpi("REALIZED P&L", esc(money(m.net)), plural(m.closed.length, "closed trade"), tone(m.net)) +
      kpi("OPEN P&L", m.open.length ? esc(money(m.unrealized)) : "–",
        m.open.length ? plural(m.open.length, "position") + " · " + esc(plain(m.riskOpen)) + " at risk" + markNote : "nothing open", m.marked ? tone(m.unrealized) : "") +
      kpi("WIN RATE", m.winRate != null ? m.winRate.toFixed(0) + "%" : "n/a",
        m.closed.length ? m.wins + " won · " + m.losses + " lost" : "needs a closed trade") +
      kpi("PROFIT FACTOR", pfText(m.profitFactor), "gross wins ÷ gross losses") +
      kpi("EXPECTANCY", m.expectancy != null ? esc(money(m.expectancy)) : "n/a",
        m.avgR != null ? "avg " + esc(rText(m.avgR)) + " per closed trade" : "per closed trade", m.expectancy != null ? tone(m.expectancy) : "") +
      kpi("MAX DRAWDOWN", m.maxDrawdown != null ? esc(m.maxDrawdown > 0 ? "-" + plain(m.maxDrawdown) : "$0") : "n/a",
        "peak-to-trough, realized", m.maxDrawdown ? "loss" : "");

    let chart;
    if (s.pts.length < 2 && !s.tail) {
      chart = "<p class='empty-note'>No position has closed yet, so the account sits at its starting " + esc(money(b.start, false)) +
        ". The curve starts at the first exit.</p>";
    } else {
      chart = lineChart(s.pts, {
        base: b.start,
        tail: s.tail,
        label: "Account value from " + money(b.start, false) + ", " + plural(m.closed.length, "closed trade") + ", now " + money(value, false),
      });
    }
    el.innerHTML = head +
      "<div class='book-grid'><div class='kpis'>" + tiles + "</div>" +
      "<figure class='curve card'><figcaption><b>Account value, exit by exit</b><span>Each step is a closed trade; the dashed line is the " +
      esc(money(b.start, false)) + " start" + (s.tail ? ", and the dotted tail adds today's open marks" : "") + ".</span></figcaption>" +
      chart + "</figure></div>";
  }

  function emptyBucket(b) {
    const flat = "<div class='flatline' role='img' aria-label='Account value flat at " + esc(money(b.start, false)) + " until the first trade'>" +
      "<span class='flat-value'>" + esc(money(b.start, false)) + "</span><span class='flat-track'><i></i></span>" +
      "<span class='flat-note'>day one · the line starts at the first fill</span></div>";
    return "<div class='empty-bucket card'>" +
      "<div><p class='eyebrow'>STARTING SOON</p><h3>" + esc(money(b.start, false)) + ", and nothing placed yet.</h3>" +
      "<p>This bucket has no trades on the ledger yet. The first fill lands here automatically with the same receipts as every other trade, and this line starts moving.</p>" +
      "<ul class='empty-facts'><li><b>" + esc(money(b.start, false)) + "</b><span>starting capital</span></li>" +
      "<li><b>$0.00</b><span>realized so far</span></li><li><b>0</b><span>open positions</span></li></ul>" +
      (BUCKETS.algo_trader && ALL.some((t) => t._bucket === "algo_trader")
        ? "<button type='button' class='ghost-button small' data-goto-bucket='algo_trader'>Meanwhile, browse the Algo Trader book →</button>" : "") +
      "</div><figure class='curve'>" + flat + "</figure></div>";
  }

  /* ---- periods + review ------------------------------------------------- */

  function renderPeriods() {
    const trades = bucketTrades();
    const groups = {};
    trades.forEach((t) => {
      const k = periodOf(t, S.grain);
      if (k) (groups[k] = groups[k] || []).push(t);
    });
    const keys = Object.keys(groups).sort().reverse();
    if (S.period && !groups[S.period]) S.period = "";
    const all = metrics(trades);
    const stats = keys.map((k) => ({ k, m: metrics(groups[k]) }));
    const max = Math.max.apply(null, stats.map((s) => Math.abs(s.m.net)).concat([1e-9]));
    const card = (key, m, label, sub) => {
      const sel = S.period === key;
      const w = key ? Math.max(3, (Math.abs(m.net) / max) * 100) : 0;
      return "<button type='button' class='period' role='radio' aria-checked='" + sel + "' data-period='" + esc(key) + "'>" +
        "<span class='period-label'>" + esc(label) + "</span>" +
        "<b class='" + tone(m.net) + "'>" + (m.closed.length ? esc(money(m.net)) : "–") + "</b>" +
        "<span class='period-sub'>" + esc(sub) + "</span>" +
        (key ? "<span class='period-bar' aria-hidden='true'><span class='" + tone(m.net) + "' style='width:" + w.toFixed(1) + "%'></span></span>" : "") +
        "</button>";
    };
    const sub = (m) => plural(m.count, "trade") + (m.winRate != null ? " · " + m.winRate.toFixed(0) + "% win" : m.open.length ? " · open" : "");
    $("periods").hidden = !trades.length;
    $("toolbar").hidden = !trades.length;
    $("period-strip").innerHTML = trades.length
      ? card("", all, "All time", sub(all)) + stats.map((s) => card(s.k, s.m, periodLabel(s.k, S.grain), sub(s.m))).join("")
      : "<p class='empty-note'>Periods appear here once this bucket has a trade.</p>";
    setRadio("period-grain", "grain", S.grain);
  }

  function renderReview() {
    const el = $("review");
    const trades = scopeTrades();
    if (!trades.length) { el.innerHTML = ""; return; }
    const m = metrics(trades);
    const title = periodLabel(S.period, S.grain, true);
    const payoff = m.avgWin != null && m.avgLoss ? m.avgWin / m.avgLoss : null;
    const tiles =
      kpi("NET REALIZED", esc(money(m.net)), plural(m.closed.length, "closed trade") + (m.avgHold != null ? " · avg hold " + m.avgHold.toFixed(1) + "d" : ""), tone(m.net)) +
      kpi("WIN RATE", m.winRate != null ? m.winRate.toFixed(0) + "%" : "n/a", m.closed.length ? m.wins + " won · " + m.losses + " lost" : "no exits yet") +
      kpi("PROFIT FACTOR", pfText(m.profitFactor), "gross " + esc(money(m.grossWin)) + " vs " + esc(money(-m.grossLoss))) +
      kpi("EXPECTANCY", m.expectancy != null ? esc(money(m.expectancy)) : "n/a", m.avgR != null ? "avg " + esc(rText(m.avgR)) + " per closed trade" : "per closed trade", m.expectancy != null ? tone(m.expectancy) : "") +
      kpi("OPEN", m.open.length ? esc(money(m.unrealized)) : "–", m.open.length ? plural(m.open.length, "position") + " · " + esc(plain(m.riskOpen)) + " at risk" : "nothing open", m.marked ? tone(m.unrealized) : "");

    // avg win vs avg loss, drawn to the same scale
    let winLoss = "";
    if (m.closed.length) {
      const big = Math.max(m.avgWin || 0, m.avgLoss || 0, 1e-9);
      winLoss = "<div class='card winloss'><h3>Average win vs average loss</h3>" +
        "<div class='wl-row'><span>Avg win</span><span class='wl-track'><span class='gain' style='width:" + (((m.avgWin || 0) / big) * 100).toFixed(1) + "%'></span></span><b class='gain'>" + (m.avgWin != null ? esc(money(m.avgWin)) : "–") + "</b></div>" +
        "<div class='wl-row'><span>Avg loss</span><span class='wl-track'><span class='loss' style='width:" + (((m.avgLoss || 0) / big) * 100).toFixed(1) + "%'></span></span><b class='loss'>" + (m.avgLoss != null ? esc(money(-m.avgLoss)) : "–") + "</b></div>" +
        "<p class='wl-note'>" + (payoff != null
          ? "Payoff ratio <b>" + payoff.toFixed(2) + "</b>: an average win is " + (payoff >= 1 ? payoff.toFixed(1) + "× " : (payoff * 100).toFixed(0) + "% of ") + "an average loss. At a " + m.winRate.toFixed(0) + "% win rate the break-even payoff is " + (m.winRate > 0 ? ((100 - m.winRate) / m.winRate).toFixed(2) : "n/a") + "."
          : m.avgLoss ? "No winners yet in this period." : "No losers yet in this period.") + "</p></div>";
    }

    let curve = "";
    if (m.closed.length) {
      let run = 0;
      const pts = [{ y: 0, date: m.closed[0]._closedOn, tip: "<b>Start of period</b><span>$0.00</span>" }];
      m.closed.forEach((t) => {
        run += t._realized;
        pts.push({ y: run, date: t._closedOn, tip: "<b>" + esc(shortDate(t._closedOn)) + " · " + esc(t.symbol) + "</b><span class='" + tone(t._realized) + "'>this exit " + esc(money(t._realized)) + "</span><span>cumulative " + esc(money(run)) + "</span>" });
      });
      curve = "<figure class='card'><figcaption><b>Cumulative realized P&amp;L</b><span>" + esc(title) + ", exit by exit.</span></figcaption>" +
        lineChart(pts, { base: 0, label: "Cumulative realized P&L for " + title + ", ending " + money(run), fmt: (v) => money(v) }) + "</figure>";
    }

    const extreme = (t, kind) => t
      ? "<button type='button' class='extreme " + kind + "' data-open='" + esc(t.id) + "'><small>" + (kind === "best" ? "BIGGEST WINNER" : "BIGGEST LOSER") + "</small>" +
        "<b class='" + tone(t._realized) + "'>" + esc(money(t._realized)) + "</b><span>" + esc(t.symbol) + " · " + esc(strategyLabel(t.strategy)) + " · " + esc(shortDate(t._closedOn)) +
        (t._r != null ? " · " + esc(rText(t._r)) : "") + "</span><i aria-hidden='true'>Open trade →</i></button>"
      : "<div class='extreme muted'><small>" + (kind === "best" ? "BIGGEST WINNER" : "BIGGEST LOSER") + "</small><b>–</b><span>" + (kind === "best" ? "no winning exit" : "no losing exit") + " in this period</span></div>";

    const byStrategy = groupBreakdown(trades, (t) => t.strategy || "unknown", strategyLabel);
    const byInst = groupBreakdown(trades, (t) => t._inst, (k) => INST_LABEL[k]);

    el.innerHTML =
      "<div class='review-head'><h3>" + esc(title) + "</h3><span>" + plural(trades.length, "trade") + " · " + m.closed.length + " closed, " + m.open.length + " open</span>" +
      "<button type='button' class='link-button' data-jump='trades'>See these " + plural(trades.length, "trade") + " ↓</button></div>" +
      "<div class='kpis compact'>" + tiles + "</div>" +
      (m.closed.length
        ? "<div class='review-grid'>" + curve + winLoss + "</div>"
        : "<p class='empty-note card'>Nothing has closed in this period yet, so there is no realized result to review. The open positions are in the list below.</p>") +
      "<div class='review-grid'>" +
      "<div class='card'><h3>P&amp;L by strategy</h3><p class='card-note'>Realized. Click a row to filter the list.</p>" + hbars(byStrategy, "data-strategy") + "</div>" +
      "<div class='card'><h3>P&amp;L by asset class</h3><p class='card-note'>Realized. Click a row to filter the list.</p>" + hbars(byInst, "data-asset") + "</div>" +
      "</div>" +
      "<div class='extremes'>" + extreme(m.best, "best") + extreme(m.worst, "worst") + "</div>";
  }

  /* ---- trade table ------------------------------------------------------ */

  function statusPill(t) {
    if (!t._closed) return "<span class='pill open'>Open</span>";
    const v = t.review && t.review.verdict;
    if (v === "scratch") return "<span class='pill flat'>Scratch</span>";
    return t._realized > 0 ? "<span class='pill win'>Win</span>" : "<span class='pill loss'>Loss</span>";
  }

  function pnlCell(t) {
    if (t._closed) return "<b class='" + tone(t._realized) + "'>" + esc(money(t._realized)) + "</b>";
    if (t._mark) return "<b class='" + tone(t._unrealized) + " unrealized' title='Unrealized, marked " + esc(utcTime(t._mark.at)) + "'>" + esc(money(t._unrealized)) + "</b><small>mark</small>";
    return "<span class='muted'>no mark</span>";
  }

  function rowHtml(t) {
    const bucketTag = S.bucket === "all" ? "<span class='btag " + t._bucket + "'>" + (t._bucket === "infinity_1k" ? "$1K" : "ALGO") + "</span>" : "";
    return "<tr data-id='" + esc(t.id) + "' class='" + (S.trade === t.id ? "current" : "") + "'>" +
      "<td class='c-date'><span>" + esc(shortDate(t._opened)) + "</span>" + (t._closed ? "<small>→ " + esc(shortDate(t._closedOn)) + "</small>" : "") + "</td>" +
      "<td class='c-sym'><button type='button' class='row-open' data-open='" + esc(t.id) + "' aria-label='Open " + esc(t.symbol) + " " + esc(strategyLabel(t.strategy)) + " opened " + esc(longDate(t._opened)) + "'>" + esc(t.symbol) + "</button>" +
      "<span class='chip " + t._inst + "'>" + INST_SHORT[t._inst] + "</span>" + bucketTag +
      "<small class='sym-sub'>" + esc(strategyLabel(t.strategy)) + "<span class='m-status'> · " + esc(!t._closed ? "open" : t.review && t.review.verdict === "scratch" ? "scratch" : t._realized > 0 ? "win" : "loss") + "</span></small></td>" +
      "<td class='c-strat col-strategy'>" + esc(strategyLabel(t.strategy)) + "</td>" +
      "<td class='c-status col-status'>" + statusPill(t) + "</td>" +
      "<td class='num c-risk col-risk'>" + (t._risk ? esc(plain(t._risk)) : "–") + "</td>" +
      "<td class='num c-r col-r'>" + esc(rText(t._r)) + "</td>" +
      "<td class='num c-pnl'>" + pnlCell(t) + "</td></tr>";
  }

  let LIST = [];

  function renderTable() {
    LIST = listTrades();
    const body = $("trade-body");
    const shown = LIST.slice(0, S.limit);
    const scoped = scopeTrades().length;
    $("result-note").textContent = LIST.length === scoped
      ? plural(LIST.length, "trade")
      : LIST.length + " of " + plural(scoped, "trade");

    if (!LIST.length) {
      body.innerHTML = "<tr class='empty-row'><td colspan='7'><b>" + (scoped ? "No trades match these filters." : "No trades here yet.") + "</b>" +
        (scoped ? "<button type='button' class='link-button' data-clear='all'>Clear filters</button>"
          : "<span>Every placed trade lands here with its thesis, structure, and receipt.</span>") + "</td></tr>";
      $("more").innerHTML = "";
    } else if (S.group === "none") {
      body.innerHTML = shown.map(rowHtml).join("");
    } else {
      const groups = [];
      const index = {};
      shown.forEach((t) => {
        const k = periodOf(t, S.group);
        if (!(k in index)) { index[k] = groups.length; groups.push({ k, rows: [] }); }
        groups[index[k]].rows.push(t);
      });
      body.innerHTML = groups.map((g) => {
        const all = LIST.filter((t) => periodOf(t, S.group) === g.k);
        const m = metrics(all);
        return "<tr class='group-row'><th colspan='7' scope='rowgroup'><span>" + esc(periodLabel(g.k, S.group, true)) + "</span>" +
          "<small>" + plural(all.length, "trade") + (m.closed.length ? " · <b class='" + tone(m.net) + "'>" + esc(money(m.net)) + "</b> realized" : "") +
          (m.winRate != null ? " · " + m.winRate.toFixed(0) + "% win" : "") + "</small></th></tr>" + g.rows.map(rowHtml).join("");
      }).join("");
    }
    const rest = LIST.length - shown.length;
    $("more").innerHTML = rest > 0
      ? "<button type='button' class='ghost-button small' data-more='1'>Show " + Math.min(rest, PAGE) + " more <span class='muted'>(" + rest + " left)</span></button>"
      : "";

    Array.prototype.forEach.call(document.querySelectorAll("#trade-table th[data-sort]"), (th) => {
      const k = th.getAttribute("data-sort");
      if (k === S.sortKey) th.setAttribute("aria-sort", S.sortDir > 0 ? "ascending" : "descending");
      else th.removeAttribute("aria-sort");
    });
    renderChips();
  }

  function renderChips() {
    const chips = [];
    if (S.period) chips.push(["period", periodLabel(S.period, S.grain, true)]);
    if (S.q.trim()) chips.push(["q", "“" + S.q.trim() + "”"]);
    if (S.status !== "all") chips.push(["status", { open: "Open", closed: "Closed", win: "Winners", loss: "Losers" }[S.status]]);
    if (S.asset !== "all") chips.push(["asset", INST_LABEL[S.asset]]);
    if (S.strategy !== "all") chips.push(["strategy", strategyLabel(S.strategy)]);
    if (S.from) chips.push(["from", "From " + longDate(S.from)]);
    if (S.to) chips.push(["to", "To " + longDate(S.to)]);
    const n = chips.filter((c) => c[0] !== "period").length;
    $("filter-toggle").textContent = n ? "Filters · " + n : "Filters";
    $("active-chips").innerHTML = chips.length
      ? chips.map((c) => "<button type='button' class='chip-x' data-clear='" + c[0] + "' aria-label='Remove filter " + esc(c[1]) + "'>" + esc(c[1]) + " <span aria-hidden='true'>✕</span></button>").join("") +
        (chips.length > 1 ? "<button type='button' class='link-button' data-clear='all'>Clear all</button>" : "")
      : "";
  }

  function syncControls() {
    $("f-search").value = S.q;
    setRadio("f-status", "v", S.status);
    $("f-asset").value = S.asset;
    $("f-from").value = S.from;
    $("f-to").value = S.to;
    $("f-group").value = S.group;
    const strategies = Array.from(new Set(bucketTrades().map((t) => t.strategy).filter(Boolean)))
      .sort((a, b) => strategyLabel(a).localeCompare(strategyLabel(b)));
    if (S.strategy !== "all" && !strategies.includes(S.strategy)) strategies.push(S.strategy);
    $("f-strategy").innerHTML = "<option value='all'>All strategies</option>" +
      strategies.map((s) => "<option value='" + esc(s) + "'>" + esc(strategyLabel(s)) + "</option>").join("");
    $("f-strategy").value = S.strategy;
    setRadio("bucket-switch", "bucket", S.bucket);
  }

  function setRadio(groupId, attr, value) {
    Array.prototype.forEach.call($(groupId).querySelectorAll("[data-" + attr + "]"), (b) => {
      const on = b.getAttribute("data-" + attr) === value;
      b.setAttribute("aria-checked", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });
  }

  /* ---- trade detail drawer ---------------------------------------------- */

  function tradePrices(t) {
    let buy = null, sell = null;
    if (t.asset_class === "equity") {
      buy = isNum(t.entry_price) ? Number(t.entry_price) : t.order && isNum(t.order.average_fill_price) ? Number(t.order.average_fill_price) : null;
      sell = t.close_order && isNum(t.close_order.average_fill_price) ? Number(t.close_order.average_fill_price) : null;
    } else {
      const qty = t.order && t.order.quantity ? Number(t.order.quantity) : 1;
      if (t.order && isNum(t.order.net_credit) && Number(t.order.net_credit) !== 0) buy = Number(t.order.net_credit) / qty;
      if (t.close_order && isNum(t.close_order.cost_to_close) && Number(t.close_order.cost_to_close) !== 0) sell = Number(t.close_order.cost_to_close) / qty;
    }
    return { buy, sell };
  }

  function legsHtml(t, prices) {
    if (t.asset_class === "equity") {
      const shares = t.order && t.order.quantity != null ? Number(t.order.quantity) : Number(t.shares) || 0;
      let out = "<li class='buy'><b>BUY</b> " + esc(plural(shares, "share")) + (prices.buy != null ? " @ " + esc(price(prices.buy)) : "") + " <small>" + esc(shortDate(t._opened)) + "</small></li>";
      if (prices.sell != null) out += "<li class='sell'><b>SELL</b> " + esc(plural(shares, "share")) + " @ " + esc(price(prices.sell)) + " <small>" + esc(shortDate(t._closedOn)) + "</small></li>";
      return out;
    }
    return (t.legs || []).map((leg) =>
      "<li class='" + (leg.action === "SELL" ? "sell" : "buy") + "'><b>" + esc(leg.action) + "</b> " + esc(leg.type) + " " + esc(leg.strike != null ? leg.strike : "") +
      (leg.expiry ? " <small>exp " + esc(longDate(leg.expiry)) + (leg.dte ? " · " + leg.dte + " DTE" : "") + "</small>" : "") + "</li>").join("");
  }

  function journeyBar(t, mark) {
    const risk = t._risk;
    if (risk <= 0) return "";
    let targetPnl = null, tpPnl = null, labels;
    if (t.asset_class === "equity") {
      const p = tradePrices(t);
      const shares = t.order && isNum(t.order.quantity) ? Number(t.order.quantity) : Number(t.shares) || 0;
      if (p.buy != null && shares > 0 && isNum(t.target_price)) targetPnl = (Number(t.target_price) - p.buy) * shares;
      labels = { low: "stop −1R", entry: "entry", target: "+2R target" };
    } else {
      if (t.max_profit) { targetPnl = Number(t.max_profit); tpPnl = targetPnl / 2; }
      const debit = t.order && Number(t.order.net_credit) < 0;
      labels = { low: "max loss", entry: debit ? "debit paid" : "credit received", tp: "50% take-profit", target: "max profit" };
    }
    const now = Number(mark.pnl);
    if (targetPnl == null) targetPnl = risk;
    const lo = -risk, hi = Math.max(targetPnl, now, 0.5 * risk);
    const pos = (v) => Math.min(Math.max((v - lo) / (hi - lo), 0), 1) * 100;
    const marker = (v, label, cls) => {
      const x = pos(v);
      const snap = x < 12 ? " snap-left" : x > 88 ? " snap-right" : "";
      return "<span class='jm " + cls + snap + "' style='left:" + x.toFixed(1) + "%'><i></i><small>" + esc(label) + "</small></span>";
    };
    const e = pos(0), n = pos(now);
    return "<div class='journey' role='img' aria-label='Position from " + esc(labels.low) + " to " + esc(labels.target) + ", now " + esc(money(now)) + "'>" +
      "<div class='journey-track'><span class='journey-fill " + tone(now) + "' style='left:" + Math.min(e, n).toFixed(1) + "%;width:" + Math.abs(n - e).toFixed(1) + "%'></span>" +
      "<span class='journey-now' style='left:" + n.toFixed(1) + "%'></span></div>" +
      "<div class='journey-marks'>" + marker(lo, labels.low, "end") + marker(0, labels.entry, "entry") +
      (tpPnl != null ? marker(tpPnl, labels.tp, "tp") : "") + marker(targetPnl, labels.target, "end") + "</div></div>";
  }

  function sparkline(t, mark) {
    const trail = (Array.isArray(t.price_trail) ? t.price_trail : []).filter((p) => p && isNum(p.price));
    if (trail.length < 2) return "";
    const values = trail.map((p) => Number(p.price));
    let lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.3; lo -= pad; hi += pad;   // a 0.1% wiggle must not read as a trend
    const W = 320, H = 56, P = 6;
    const x = (i) => P + (i / (trail.length - 1)) * (W - 2 * P);
    const y = (v) => H - P - ((v - lo) / (hi - lo)) * (H - 2 * P);
    const up = mark ? Number(mark.pnl) >= 0 : values[values.length - 1] >= values[0];
    const p = tradePrices(t);
    const entry = t.asset_class === "equity" ? p.buy : null;
    const verb = t.asset_class === "equity" ? "Price" : "Cost to close";
    return "<svg class='spark' viewBox='0 0 " + W + " " + H + "' preserveAspectRatio='none' role='img' aria-label='" +
      esc(verb + " since entry, " + trail.length + " marks, latest " + price(values[values.length - 1], t.asset_class === "equity" ? 2 : 3)) + "'>" +
      (entry != null && entry >= lo && entry <= hi ? "<line class='base' x1='" + P + "' x2='" + (W - P) + "' y1='" + y(entry).toFixed(1) + "' y2='" + y(entry).toFixed(1) + "'/>" : "") +
      "<polyline class='spark-line " + (up ? "gain" : "loss") + "' points='" + values.map((v, i) => x(i).toFixed(1) + "," + y(v).toFixed(1)).join(" ") + "'/>" +
      "<circle class='spark-dot " + (up ? "gain" : "loss") + "' cx='" + x(trail.length - 1).toFixed(1) + "' cy='" + y(values[values.length - 1]).toFixed(1) + "' r='3.5'/></svg>";
  }

  function condDisplay(key, value) {
    if (value == null || value === "") return null;
    const n = Number(value);
    switch (key) {
      case "trend": return value === "above_200d" ? "Above 200d" : value === "below_200d" ? "Below 200d" : String(value).replace(/_/g, " ");
      case "pop": return isNaN(n) ? null : (n > 0 && n <= 1 ? n * 100 : n).toFixed(0) + "%";
      case "expected_move_pct": return isNaN(n) ? null : "±" + n.toFixed(1) + "%";
      case "edge_pct": return isNaN(n) ? null : (n >= 0 ? "+" : "") + Math.round(n * 100) / 100 + "%";
      case "vix": case "atr_14": return isNaN(n) ? null : n.toFixed(2);
      case "price": return isNaN(n) ? null : "$" + n.toFixed(2);
      case "score": case "composite_score": case "confidence_score": case "iv_rank": case "rsi_14": case "adx_14":
        return isNaN(n) ? null : String(Math.round(n * 10) / 10);
      default: return String(value).replace(/_/g, " ");
    }
  }

  function conditionsHtml(t) {
    const c = t.conditions;
    if (!c) return "";
    const specs = c.engine === "equity"
      ? [["Score", "score"], ["Read", "signal"], ["Trend", "trend"], ["RSI 14", "rsi_14"], ["ADX 14", "adx_14"], ["ATR 14", "atr_14"], ["Risk tilt", "risk_tilt"], ["Entry", "price"]]
      : [["Regime", "regime"], ["VIX", "vix"], ["IV rank", "iv_rank"], ["Exp. move", "expected_move_pct"], ["Score", "composite_score"], ["Confidence", "confidence_score"], ["POP", "pop"], ["Edge", "edge_pct"]];
    const chips = specs.map((s) => {
      const v = condDisplay(s[1], c[s[1]]);
      return v == null ? "" : "<li><small>" + esc(s[0]) + "</small><b>" + esc(v) + "</b></li>";
    }).join("");
    if (!chips) return "";
    const rules = [["Entry rules", c.entry_rules], ["Exit rules", c.exit_rules]].map((pair) => {
      const r = pair[1];
      if (!r) return "";
      const items = Array.isArray(r) ? r.map((x) => [null, x]) : Object.keys(r).map((k) => [k.replace(/_/g, " "), r[k]]);
      if (!items.length) return "";
      return "<details class='rules'><summary>" + pair[0] + "</summary><dl>" +
        items.map((i) => (i[0] ? "<dt>" + esc(i[0]) + "</dt>" : "") + "<dd>" + esc(i[1]) + "</dd>").join("") + "</dl></details>";
    }).join("");
    return "<section class='d-sec'><h4>Entry conditions</h4><ul class='conds'>" + chips + "</ul>" + rules +
      "<p class='d-note'>What the quality gates saw when this trade was recommended" + (c.captured_at ? ", captured " + esc(longDate(c.captured_at)) : "") + ".</p></section>";
  }

  function planHtml(t) {
    const p = t.management_plan;
    if (!p) return "";
    const rows = [["Target", p.target], ["Stop", p.stop], ["Time", p.time], ["Event", p.event]].filter((r) => r[1]);
    if (!rows.length) return "";
    return "<section class='d-sec'><h4>Management plan</h4><dl class='plan'>" +
      rows.map((r) => "<dt>" + r[0] + "</dt><dd>" + esc(r[1]) + "</dd>").join("") + "</dl></section>";
  }

  function reviewHtml(t) {
    const r = t.review;
    if (!r || typeof r !== "object") return "";
    const verdict = ["win", "loss", "scratch"].includes(r.verdict) ? r.verdict : null;
    const drivers = Array.isArray(r.drivers) ? r.drivers.filter(Boolean) : [];
    if (!verdict && !drivers.length && !r.lesson) return "";
    return "<section class='d-sec post-review'><h4>Post-trade review" +
      (verdict ? " <span class='pill " + (verdict === "scratch" ? "flat" : verdict) + "'>" + esc(verdict) + "</span>" : "") + "</h4>" +
      (drivers.length ? "<p class='d-label'>What drove it</p><ul class='drivers'>" + drivers.map((d) => "<li>" + esc(d) + "</li>").join("") + "</ul>" : "") +
      (r.lesson ? "<p class='d-label'>Lesson</p><blockquote>" + esc(r.lesson) + "</blockquote>" : "") + "</section>";
  }

  function receiptHtml(t) {
    if (t.source !== "ledger" || !t.order) return "<p class='receipt'>" + esc(t.timestamp) + "</p>";
    const o = t.order;
    const line = (label, ord, extra) => {
      const bits = [];
      if (ord.status) bits.push(ord.status);
      if (ord.filled != null && ord.quantity != null) bits.push(ord.filled + "/" + ord.quantity + " filled");
      if (ord.average_fill_price != null) bits.push("@ " + ord.average_fill_price);
      if (ord.limit_price != null) bits.push("limit " + ord.limit_price);
      (extra || []).forEach((b) => b && bits.push(b));
      return "<li><b>" + label + "</b><span>" + esc(bits.join(" · ")) + "</span><small>" + esc(ord.updated_at || ord.submitted_at || "") + "</small></li>";
    };
    const entryExtra = [isNum(o.net_credit) && Number(o.net_credit) !== 0 ? (Number(o.net_credit) > 0 ? "credit $" : "debit $") + Math.abs(Number(o.net_credit)) : null];
    const c = t.close_order;
    const closeExtra = c ? [
      isNum(c.cost_to_close) && Number(c.cost_to_close) !== 0 ? "closed for $" + c.cost_to_close : null,
      isNum(c.realized_pnl) ? "realized " + money(c.realized_pnl) : null,
      c.reason ? String(c.reason).replace(/_/g, " ") : null] : [];
    return "<section class='d-sec'><h4>Receipts</h4><ul class='receipts'>" + line("Entry", o, entryExtra) + (c ? line("Exit", c, closeExtra) : "") + "</ul>" +
      "<p class='receipt'>ledger <code>" + esc(t.ledger_ref || t.source_id) + "</code>" + (t.carried ? " · carried from the prior ledger" : "") + "</p></section>";
  }

  function drawerHtml(t) {
    const prices = tradePrices(t);
    const isEq = t.asset_class === "equity";
    const b = BUCKETS[t._bucket];
    const acct = b ? b.start : 0;
    const head =
      "<header class='d-head'><div class='d-title'><h2 id='drawer-title'>" + esc(t.symbol) + "</h2>" +
      "<span class='chip " + t._inst + "'>" + esc(INST_LABEL[t._inst]) + "</span>" + statusPill(t) +
      "<span class='btag " + t._bucket + "'>" + esc(b ? b.label : "") + "</span></div>" +
      "<p class='d-strategy'>" + esc(strategyLabel(t.strategy)) +
      (t.dte_at_entry ? " · " + t.dte_at_entry + " DTE at entry" : "") +
      (!isEq && t.entry_ivr != null ? " · IVR " + esc(t.entry_ivr) : "") +
      (!isEq && t.expected_move_pct != null ? " · expected move ±" + esc(t.expected_move_pct) + "%" : "") + "</p>" +
      "<div class='d-pnl'>" +
      (t._closed
        ? "<div><small>NET P&amp;L</small><b class='" + tone(t._realized) + "'>" + esc(money(t._realized)) + "</b><span>" +
          (isEq ? (t._r != null ? esc(rText(t._r)) + " on risk" : "realized") : esc(pctText(t.net_pnl_pct)) + " of max profit" + (t._r != null ? " · " + esc(rText(t._r)) : "")) + "</span></div>"
        : t._mark
          ? "<div><small>UNREALIZED</small><b class='" + tone(t._unrealized) + "'>" + esc(money(t._unrealized)) + "</b><span>" + esc(pctText(t._mark.pnl_pct)) + " of risk · " + esc(rText(t._r)) + " · marked " + esc(utcTime(t._mark.at)) + "</span></div>"
          : "<div><small>POSITION</small><b>open</b><span>waiting for the first price mark</span></div>") +
      "<dl class='facts'>" +
      "<div><dt>" + (isEq ? "Bought" : "Opened") + "</dt><dd>" + esc(longDate(t._opened)) + "<small>" +
        esc(isEq ? (prices.buy != null ? "@ " + price(prices.buy) : "") : prices.buy != null ? (prices.buy > 0 ? "credit " : "debit ") + price(Math.abs(prices.buy)) + "/ctr" : "") + "</small></dd></div>" +
      "<div><dt>" + (isEq ? "Sold" : "Closed") + "</dt><dd>" + (t._closed ? esc(longDate(t._closedOn)) : "still open") + "<small>" +
        esc(prices.sell != null ? (isEq ? "@ " : "") + price(prices.sell) + (isEq ? "" : "/ctr") : t._mark ? "now " + price(t._mark.price, isEq ? 2 : 3) + (isEq ? "" : " to close") : "") + "</small></dd></div>" +
      "<div><dt>Risk</dt><dd>" + (t._risk ? esc(plain(t._risk)) : "n/a") + "<small>" + (t._risk && acct ? ((t._risk / acct) * 100).toFixed(1) + "% of " + esc(money(acct, false)) : "") + "</small></dd></div>" +
      "<div><dt>" + (isEq ? "Stop / target" : "Max profit") + "</dt><dd>" +
        (isEq ? esc(isNum(t.stop_price) ? price(t.stop_price) : "n/a") + " / " + esc(isNum(t.target_price) ? price(t.target_price) : "n/a")
          : esc(t.max_profit != null ? plain(t.max_profit) : "n/a")) + "<small>" + (t._hold != null ? "held " + plural(Math.round(t._hold), "day") : "") + "</small></dd></div>" +
      "</dl></div></header>";

    let journey = "";
    if (!t._closed) {
      const bar = t._mark ? journeyBar(t, t._mark) : "";
      const spark = sparkline(t, t._mark);
      if (bar || spark) {
        const n = Array.isArray(t.price_trail) ? t.price_trail.length : 0;
        journey = "<section class='d-sec'><h4>" + (isEq ? "Price journey since entry" : "Mark since entry") + "</h4>" + bar + spark +
          "<p class='d-note'>Delayed-venue marks refreshed by the engine" + (n > 1 ? " · " + n + " snapshots" : "") +
          (t._mark && utcTime(t._mark.at) ? " · latest " + esc(utcTime(t._mark.at)) : "") + "</p></section>";
      }
    }

    const tags = (t.tags || []).map((g) => "<span>#" + esc(g) + "</span>").join("");
    const links = (t.research || []).map((l) => "<a href='" + esc(l.url) + "' target='_blank' rel='noreferrer'>" + esc(l.label) + " ↗</a>").join("");

    return head +
      reviewHtml(t) +
      "<section class='d-sec'><h4>The why</h4><p>" + esc((t.conditions && t.conditions.reasoning) || t.reason) + "</p>" +
      (t.conditions && t.conditions.reasoning && t.reason ? "<p class='d-note'>" + esc(t.reason) + "</p>" : "") + "</section>" +
      "<section class='d-sec'><h4>What happened</h4><p>" + esc(t.exit_note) + "</p></section>" +
      "<section class='d-sec'><h4>" + (isEq ? "Orders" : "Legs") + "</h4><ul class='legs'>" + legsHtml(t, prices) + "</ul></section>" +
      journey + planHtml(t) + conditionsHtml(t) +
      (tags || links ? "<section class='d-sec'><div class='tags'>" + tags + "</div><div class='links'>" + links + "</div></section>" : "") +
      receiptHtml(t);
  }

  let lastFocus = null;

  function openTrade(id, fromEl) {
    const t = ALL.find((x) => x.id === id);
    const dlg = $("drawer");
    if (!t) { S.trade = ""; writeHash(); return; }
    if (fromEl) lastFocus = fromEl;
    S.trade = id;
    $("drawer-body").innerHTML = drawerHtml(t);
    $("drawer-body").scrollTop = 0;
    const i = LIST.findIndex((x) => x.id === id);
    $("drawer-prev").disabled = i <= 0;
    $("drawer-next").disabled = i < 0 || i >= LIST.length - 1;
    $("drawer-pos").textContent = i >= 0 ? i + 1 + " / " + LIST.length : "";
    if (!dlg.open) {
      if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
      document.documentElement.classList.add("drawer-open");
    }
    markCurrentRow();
    writeHash();
  }

  function closeTrade() {
    const dlg = $("drawer");
    S.trade = "";
    if (dlg.open) { if (typeof dlg.close === "function") dlg.close(); else dlg.removeAttribute("open"); }
    document.documentElement.classList.remove("drawer-open");
    markCurrentRow();
    writeHash();
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }

  function stepTrade(delta) {
    const i = LIST.findIndex((x) => x.id === S.trade);
    const next = LIST[i + delta];
    if (next) openTrade(next.id);
  }

  function markCurrentRow() {
    Array.prototype.forEach.call(document.querySelectorAll("#trade-body tr[data-id]"), (tr) =>
      tr.classList.toggle("current", tr.getAttribute("data-id") === S.trade));
  }

  /* ---- render orchestration --------------------------------------------- */

  function renderAll() {
    chartSeq = 0;
    Object.keys(CHARTS).forEach((k) => delete CHARTS[k]);
    syncControls();
    renderBook();
    renderPeriods();
    renderReview();
    renderTable();
    writeHash();
  }

  function renderScoped() {   // after a period/table-filter change: the book header is unaffected
    Object.keys(CHARTS).forEach((k) => { if (!document.querySelector("#book [data-chart='" + k + "']")) delete CHARTS[k]; });
    renderPeriods();
    renderReview();
    renderTable();
    writeHash();
  }

  function render(data) {
    DATA = data;
    BUCKETS = buildBuckets(data);
    ALL = (data.trades || []).map(normalize);
    const trader = data.trader || {};
    $("journal-sub").textContent =
      (trader.tagline ? trader.tagline + " · " : "") + (trader.name || "") + (trader.handle ? " (" + trader.handle + ")" : "") +
      (data.as_of ? " · journal updated " + longDate(data.as_of) : "") +
      ". Every placed trade appears here, winners and losers. Nothing is filtered.";
    const v = data.verification;
    if (v && v.ledger_sha) {
      let text = "Every ledger entry is derived from the paper-order ledger (sha " + v.ledger_sha.slice(0, 12) + ")" +
        (data.as_of ? " on " + data.as_of : "") + ". Recomputable, never edited by hand.";
      const carried = Number(v.entries_carried) || 0;
      if (carried > 0 && v.prior_ledger_sha) {
        text += " " + plural(carried, "earlier entry", "earlier entries") + " carried from the prior ledger (sha " +
          String(v.prior_ledger_sha).slice(0, 12) + ") and kept verbatim.";
      }
      $("verify-note").textContent = text;
    }
    renderAll();
    if (S.trade) {
      if (ALL.some((t) => t.id === S.trade)) openTrade(S.trade); else closeTrade();
    }
  }

  function renderFailure() {
    $("book").innerHTML = "<div class='card empty-note'><b>The journal data could not be loaded.</b> It refreshes every few minutes; try reloading the page.</div>";
    $("period-strip").innerHTML = "";
    $("review").innerHTML = "";
    $("trade-body").innerHTML = "<tr class='empty-row'><td colspan='7'>No trades to show.</td></tr>";
  }

  function load() {
    fetch("./trades.json", { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.text(); })
      .then((text) => {
        if (text === lastPayload) return;   // nothing changed VM-side; skip re-render
        const data = JSON.parse(text);
        lastPayload = text;
        render(data);
      })
      .catch(() => { if (!DATA) renderFailure(); });
  }

  /* ---- events ----------------------------------------------------------- */

  function scrollToTrades() {
    const el = $("trades");
    el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    el.focus({ preventScroll: true });
  }

  function radioKeys(container, onPick) {
    container.addEventListener("keydown", (e) => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return;
      const items = Array.prototype.slice.call(container.querySelectorAll("[role=radio]"));
      let i = items.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      if (e.key === "Home") i = 0;
      else if (e.key === "End") i = items.length - 1;
      else i = (i + (e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
      onPick(items[i]);
      const again = container.querySelectorAll("[role=radio]")[i];
      if (again) again.focus();
    });
  }

  function wire() {
    const pickBucket = (btn) => {
      const v = btn.getAttribute("data-bucket");
      if (!v || v === S.bucket) return;
      S.bucket = v; S.period = ""; S.strategy = "all"; S.limit = PAGE;
      renderAll();
    };
    $("bucket-switch").addEventListener("click", (e) => { const b = e.target.closest("[data-bucket]"); if (b) pickBucket(b); });
    radioKeys($("bucket-switch"), pickBucket);

    const pickGrain = (btn) => {
      const v = btn.getAttribute("data-grain");
      if (!v || v === S.grain) return;
      S.grain = v; S.period = ""; S.limit = PAGE;
      renderScoped();
    };
    $("period-grain").addEventListener("click", (e) => { const b = e.target.closest("[data-grain]"); if (b) pickGrain(b); });
    radioKeys($("period-grain"), pickGrain);

    const pickPeriod = (btn) => {
      const v = btn.getAttribute("data-period");
      if (v == null) return;
      S.period = v; S.limit = PAGE;
      renderScoped();
    };
    $("period-strip").addEventListener("click", (e) => {
      const b = e.target.closest("[data-period]");
      if (!b) return;
      pickPeriod(b);
      const again = $("period-strip").querySelector("[data-period='" + CSS.escape(b.getAttribute("data-period")) + "']");
      if (again) again.focus({ preventScroll: true });
    });
    radioKeys($("period-strip"), pickPeriod);

    const pickStatus = (btn) => { S.status = btn.getAttribute("data-v"); S.limit = PAGE; setRadio("f-status", "v", S.status); renderTable(); };
    $("f-status").addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (b) pickStatus(b); });
    radioKeys($("f-status"), pickStatus);

    let searchTimer = 0;
    $("f-search").addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { S.q = e.target.value; S.limit = PAGE; renderTable(); }, 120);
    });
    [["f-asset", "asset"], ["f-strategy", "strategy"], ["f-from", "from"], ["f-to", "to"], ["f-group", "group"]].forEach((pair) => {
      $(pair[0]).addEventListener("change", (e) => { S[pair[1]] = e.target.value; S.limit = PAGE; renderTable(); });
    });

    $("filter-toggle").addEventListener("click", () => {
      const open = $("toolbar").classList.toggle("collapsed") === false;
      $("filter-toggle").setAttribute("aria-expanded", open ? "true" : "false");
    });

    // back/forward or a pasted link changing the hash
    window.addEventListener("hashchange", () => {
      const prev = { b: S.bucket, g: S.grain, p: S.period };
      S.bucket = DEFAULT_BUCKET; S.grain = "month"; S.period = ""; S.trade = "";
      readHash();
      if (!DATA) return;
      if (prev.b !== S.bucket || prev.g !== S.grain || prev.p !== S.period) renderAll();
      if (S.trade) openTrade(S.trade); else if ($("drawer").open) closeTrade();
    });

    document.querySelector("#trade-table thead").addEventListener("click", (e) => {
      const th = e.target.closest("th[data-sort]");
      if (!th) return;
      const k = th.getAttribute("data-sort");
      if (S.sortKey === k) S.sortDir = -S.sortDir;
      else { S.sortKey = k; S.sortDir = k === "symbol" || k === "strategy" ? 1 : -1; }
      renderTable();
    });

    $("trade-body").addEventListener("click", (e) => {
      const clr = e.target.closest("[data-clear]");
      if (clr) return;   // handled by the document listener
      const tr = e.target.closest("tr[data-id]");
      if (tr) openTrade(tr.getAttribute("data-id"), tr.querySelector(".row-open"));
    });

    document.addEventListener("click", (e) => {
      const opener = e.target.closest("[data-open]");
      if (opener && !opener.closest("#trade-body")) { openTrade(opener.getAttribute("data-open"), opener); return; }
      const clr = e.target.closest("[data-clear]");
      if (clr) {
        const k = clr.getAttribute("data-clear");
        if (k === "all" || k === "period") S.period = "";
        if (k === "all" || k === "q") S.q = "";
        if (k === "all" || k === "status") S.status = "all";
        if (k === "all" || k === "asset") S.asset = "all";
        if (k === "all" || k === "strategy") S.strategy = "all";
        if (k === "all" || k === "from") S.from = "";
        if (k === "all" || k === "to") S.to = "";
        S.limit = PAGE;
        syncControls();
        if (k === "all" || k === "period") renderScoped(); else renderTable();
        return;
      }
      if (e.target.closest("[data-more]")) { S.limit += PAGE; renderTable(); return; }
      if (e.target.closest("[data-jump='trades']")) { scrollToTrades(); return; }
      const strat = e.target.closest("#review [data-strategy]");
      if (strat) { S.strategy = strat.getAttribute("data-strategy"); S.limit = PAGE; syncControls(); renderTable(); scrollToTrades(); return; }
      const asset = e.target.closest("#review [data-asset]");
      if (asset) { S.asset = asset.getAttribute("data-asset"); S.limit = PAGE; syncControls(); renderTable(); scrollToTrades(); return; }
      const gb = e.target.closest("[data-goto-bucket]");
      if (gb) { S.bucket = gb.getAttribute("data-goto-bucket"); S.period = ""; renderAll(); }
    });

    const dlg = $("drawer");
    $("drawer-close").addEventListener("click", closeTrade);
    $("drawer-prev").addEventListener("click", () => stepTrade(-1));
    $("drawer-next").addEventListener("click", () => stepTrade(1));
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeTrade(); });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) closeTrade(); });   // backdrop
    dlg.addEventListener("keydown", (e) => {
      if (e.target.closest("input, select, textarea, summary")) return;
      if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); stepTrade(1); }
      if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); stepTrade(-1); }
    });

    // chart hover: one shared tooltip, nearest point by x
    const tip = $("chart-tip");
    const hideTip = () => {
      tip.hidden = true;
      document.querySelectorAll(".chart .cross").forEach((n) => n.setAttribute("visibility", "hidden"));
      document.querySelectorAll(".chart .hover-dot").forEach((n) => { n.hidden = true; });
    };
    document.addEventListener("pointermove", (e) => {
      const wrap = e.target.closest && e.target.closest("[data-chart]");
      if (!wrap) { if (!tip.hidden) hideTip(); return; }
      const c = CHARTS[wrap.getAttribute("data-chart")];
      if (!c || !c.hover.length) return;
      const rect = wrap.querySelector(".plot").getBoundingClientRect();
      const vx = ((e.clientX - rect.left) / rect.width) * c.W;
      let best = c.hover[0];
      c.hover.forEach((p) => { if (Math.abs(p.x - vx) < Math.abs(best.x - vx)) best = p; });
      const cross = wrap.querySelector(".cross"), dot = wrap.querySelector(".hover-dot");
      cross.setAttribute("x1", best.x); cross.setAttribute("x2", best.x); cross.setAttribute("visibility", "visible");
      dot.style.left = (best.x / c.W) * 100 + "%"; dot.style.top = (best.y / c.H) * 100 + "%"; dot.hidden = false;
      tip.innerHTML = best.tip;
      tip.hidden = false;
      const px = rect.left + (best.x / c.W) * rect.width;
      const py = rect.top + (best.y / c.H) * rect.height;
      let left = px + 14, top = py - tip.offsetHeight - 10;
      if (left + tip.offsetWidth > window.innerWidth - 8) left = px - tip.offsetWidth - 14;
      if (top < 8) top = py + 14;
      tip.style.left = Math.max(8, left) + "px";
      tip.style.top = top + "px";
    });
    document.addEventListener("pointerleave", hideTip);
    window.addEventListener("scroll", () => { if (!tip.hidden) hideTip(); }, { passive: true });

    // theme: auto -> light -> dark -> auto
    const themeBtn = $("theme-toggle");
    const labelTheme = () => {
      const t = document.documentElement.getAttribute("data-theme") || "auto";
      themeBtn.setAttribute("aria-label", "Color theme: " + t);
      themeBtn.title = "Color theme: " + t + " (click to change)";
      themeBtn.firstElementChild.textContent = t === "light" ? "☀" : t === "dark" ? "☾" : "◐";
    };
    themeBtn.addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") || "auto";
      const next = cur === "auto" ? "light" : cur === "light" ? "dark" : "auto";
      if (next === "auto") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", next);
      try { if (next === "auto") localStorage.removeItem("tf-theme"); else localStorage.setItem("tf-theme", next); } catch (e) { /* storage blocked */ }
      labelTheme();
    });
    labelTheme();
  }

  readHash();
  wire();
  load();
  // The VM bakes fresh price marks into trades.json hourly (journal timer), and
  // a five-minute same-origin re-fetch keeps an open tab no more than ~5 min
  // behind the latest published snapshot.
  setInterval(load, LIVE_REFETCH_MS);
})();
