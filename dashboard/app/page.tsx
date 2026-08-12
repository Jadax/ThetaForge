"use client";

import { FormEvent, useEffect, useState } from "react";

type Analysis = {
  symbol: string;
  stock_price: number;
  overall_signal: string;
  overall_score: number;
  confidence: number;
  regime: string;
  best_strategy: string;
  best_strategy_reasoning: string;
  all_signals: Array<{ source: string; signal: string; confidence: number; reasoning: string }>;
  recommendations_1m: Array<{ strategy: string; suitability: number; typical_dte: string; typical_delta: string }>;
};

type BridgePosition = { id?: string; symbol: string; position: number; average_cost: number; contract_type?: string; strike?: number | null; expiry?: string | null; right?: string | null };

type TradeRecommendation = {
  id: string;
  symbol: string;
  strategy: string;
  underlying_price: number;
  legs: Array<{ action: string; strike: number; expiry: string; type: string }>;
  quantity: number;
  capital_required: number;
  max_loss: number;
  max_profit: number;
  net_credit: number;
  net_debit: number;
  breakeven: number;
  probability_of_profit: number;
  theoretical_edge_pct?: number;
  model_value?: number;
  expected_move_pct: number;
  composite_score: number;
  confidence_score: number;
  iv_rank: number;
  vix: number;
  market_regime: string;
  return_on_capital_pct: number;
  annualized_return_pct: number;
  reasoning: string;
  risk_warning: string;
  entry_rules: Record<string, string>;
  exit_rules: Record<string, string>;
  pricing_status?: string;
};

type IBKRQuote = {
  symbol: string;
  expiry: string;
  strike: number;
  right: "C" | "P";
  bid: number | null;
  ask: number | null;
  last: number | null;
  market_data_type: "live" | "frozen" | "delayed" | "delayed_frozen" | "unavailable";
  executable: boolean;
};

type BrainNotification = {
  id: string;
  symbol: string;
  score: number;
  signal: string;
  regime: string;
  best_strategy: string;
  strategy_reasoning: string;
  iv_rank?: number | null;
  iv_hv_ratio?: number | null;
  iv_hv_signal?: string | null;
  top_signal: string;
  timestamp: string;
  acknowledged: boolean;
};

type ScannerStatus = {
  last_run: string | null;
  symbols_scanned_last_run: number;
  symbols_with_trades: number;
  last_results?: { symbols?: Record<string, unknown> };
};

type PaperOrderRecord = {
  id: string;
  symbol: string;
  strategy?: string | null;
  quantity: number;
  status: string;
  filled: number;
  remaining: number;
  limit_price: number;
  max_loss_total: number;
  submitted_at: string;
};

type PaperOrderLedger = {
  week_key: string;
  capital_limit: number | null;
  capital_reserved: number;
  capital_remaining: number | null;
  orders: PaperOrderRecord[];
};

type RecommendationResponse = {
  recommendations: TradeRecommendation[];
  warnings: string[];
  market_context: { regime: string; vix: number };
  universe_size?: number;
  active_discoveries?: number;
  shortlisted_symbols?: string[];
};

const signalLabel = (signal: string) => signal.replaceAll("_", " ");
const dollars = (value: number) => `$${Math.max(0, value || 0).toFixed(0)}`;
const percent = (value: number) => `${Math.max(0, value || 0).toFixed(0)}%`;

// Expected-move price band + P/L zones, drawn on the underlying's price axis
// (Options AI / OptionStrat pattern). Fail-closed: renders an empty placeholder
// when the expected move or strikes are missing.
function ExpectedMoveChart({ trade }: { trade: TradeRecommendation }) {
  const px = trade.underlying_price;
  const em = trade.expected_move_pct;
  if (!px || !em || !trade.legs || trade.legs.length === 0) {
    return <div className="em-chart em-chart-empty">Expected move data unavailable for this structure.</div>;
  }
  const strikes = trade.legs.map((l) => l.strike).filter((s) => s && s > 0);
  const shortLeg = trade.legs.find((l) => l.action === "SELL");
  const safeSideUp = shortLeg?.type === "PUT";
  const span = Math.max(em * 1.7, 3.0) / 100;
  const lo = px * (1 - span);
  const hi = px * (1 + span);
  const W = 220;
  const PAD = 8;
  const x = (price: number) => PAD + ((price - lo) / (hi - lo)) * (W - PAD * 2);
  const bandLo = px * (1 - em / 100);
  const bandHi = px * (1 + em / 100);
  const inBand = (p: number) => p >= bandLo && p <= bandHi;
  // Count how many strikes sit outside the expected-move band on the safe side.
  const safeStrikes = trade.legs.filter((l) =>
    safeSideUp ? l.strike >= bandHi : l.strike <= bandLo
  ).length;
  const ticks = [px * 0.97, px * 0.985, px, px * 1.015, px * 1.03].filter(
    (t) => t >= lo && t <= hi && Math.floor(t * 100) % 2 === 0
  );
  return (
    <div className="em-chart">
      <svg viewBox={`0 0 ${W} 66`} role="img" aria-label="Expected move and P/L zones on the price axis">
        {[bandLo, bandHi].map((band, i) => (
          <line key={i} x1={x(band)} y1="10" x2={x(band)} y2="56" stroke="#4c6754" strokeWidth="1" strokeDasharray="2 2" />
        ))}
        <rect x={x(bandLo)} y="10" width={Math.max(x(bandHi) - x(bandLo), 2)} height="46" fill="#c9ff5d" opacity="0.10" />
        <line x1={x(px)} y1="10" x2={x(px)} y2="56" stroke="#e8ece9" strokeWidth="1.4" />
        {strikes.map((s, i) => (
          <line key={i} x1={x(s)} y1="14" x2={x(s)} y2="52" stroke="#f0a79b" strokeWidth="2" />
        ))}
        {strikes.filter(inBand).length > 0 && (
          <text x={x(px)} y="64" fill="#f0a79b" fontSize="7" textAnchor="middle">short wing inside expected move</text>
        )}
      </svg>
      <div className="em-caption">
        <span><i className="em-dot price" /> underlying {dollars(px)}</span>
        <span><i className="em-dot move" /> exp move ±{em.toFixed(1)}%</span>
        <span className={safeStrikes > 0 ? "safe" : "warn"}>{safeStrikes > 0 ? `${safeStrikes} short outside the move` : "short inside the move"}</span>
      </div>
    </div>
  );
}
const quoteKey = (symbol: string, expiry: string, strike: number, right: string) => `${symbol}|${expiry}|${strike}|${right}`;
const DEFAULT_ADVISOR_API = "https://thetaforge-advisor.onrender.com";
const NON_ACTIONABLE_STRATEGIES = new Set(["no_trade", "avoid_new_positions", "roll_or_close"]);
const ALERT_SCORE_FLOOR = 75;
const VERSION = "v1.3.0";

export default function Home() {
  const [symbol, setSymbol] = useState("SPY");
  const [apiBase, setApiBase] = useState(DEFAULT_ADVISOR_API);
  const [advisorToken, setAdvisorToken] = useState("");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [status, setStatus] = useState("Advisor not connected");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [bridgeBase, setBridgeBase] = useState("http://127.0.0.1:8002");
  const [bridgeToken, setBridgeToken] = useState("");
  const [bridgeStatus, setBridgeStatus] = useState("Bridge not connected");
  const [positions, setPositions] = useState<BridgePosition[]>([]);
  const [bridgeLoading, setBridgeLoading] = useState(false);
  const [maxOptionsCapital, setMaxOptionsCapital] = useState("");
  const [topTrades, setTopTrades] = useState<RecommendationResponse | null>(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState("");
  const [selectedStock, setSelectedStock] = useState("");
  const [stockTrades, setStockTrades] = useState<RecommendationResponse | null>(null);
  const [stockLoading, setStockLoading] = useState(false);
  const [paperOrderStatus, setPaperOrderStatus] = useState<Record<string, string>>({});
  const [submittingTrade, setSubmittingTrade] = useState<string | null>(null);
  const [notifications, setNotifications] = useState<BrainNotification[]>([]);
  const [scannerStatus, setScannerStatus] = useState<ScannerStatus | null>(null);
  const [showNotifications, setShowNotifications] = useState(false);
  const [paperOrders, setPaperOrders] = useState<PaperOrderRecord[]>([]);
  const [capitalReserved, setCapitalReserved] = useState(0);
  const [capitalRemaining, setCapitalRemaining] = useState<number | null>(null);
  const [alertDetailOpen, setAlertDetailOpen] = useState(false);
  const [alertDetailSymbol, setAlertDetailSymbol] = useState("");

  // The hosted Advisor holds one shared watchlist, alert set, and notification
  // queue. Every call carries the shared secret so that a public URL does not
  // mean public control of this account's state.
  // On the first render the token has been read from session storage but not
  // yet applied to state, so callers running at mount pass it explicitly.
  function advisorRequest(path: string, init: RequestInit = {}, tokenOverride?: string) {
    const token = tokenOverride ?? advisorToken;
    const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
    if (init.body) headers["Content-Type"] = "application/json";
    if (token) headers["X-ThetaForge-Advisor-Token"] = token;
    return fetch(`${apiBase.replace(/\/$/, "")}${path}`, { ...init, headers });
  }

  useEffect(() => {
    const saved = window.localStorage.getItem("thetaforge-api-base");
    const advisorBase = saved === "http://localhost:8000" || !saved ? DEFAULT_ADVISOR_API : saved;
    const savedToken = window.localStorage.getItem("thetaforge-advisor-token") || "";
    setApiBase(advisorBase);
    setAdvisorToken(savedToken);
    setBridgeBase(window.localStorage.getItem("thetaforge-bridge-base") || "http://127.0.0.1:8002");
    setBridgeToken(window.localStorage.getItem("thetaforge-bridge-token") || "");
    const savedCapital = window.localStorage.getItem("thetaforge-max-options-capital") || "";
    setMaxOptionsCapital(savedCapital);
    void checkAdvisor(advisorBase);
    // Without a token the scan can only return 401, so wait for the user to
    // supply one rather than opening with a failure.
    if (savedToken && Number(savedCapital) > 0) {
      void fetchAutomaticOpportunities(Number(savedCapital), savedToken);
    }
  }, []);

  useEffect(() => {
    if (!apiBase || !advisorToken) return;
    const poll = async () => {
      try {
        const res = await advisorRequest("/api/advisor/notifications?unacknowledged_only=true&limit=20");
        if (res.ok) {
          const data = await res.json() as { notifications: BrainNotification[] };
          setNotifications(
            (data.notifications || []).filter(
              (notification) => !NON_ACTIONABLE_STRATEGIES.has(notification.best_strategy)
                && Math.abs(notification.score) >= ALERT_SCORE_FLOOR,
            ),
          );
        }
        const statusResponse = await advisorRequest("/api/advisor/scanner/status");
        if (statusResponse.ok) setScannerStatus(await statusResponse.json() as ScannerStatus);
      } catch { /* backend may be down */ }
    };
    void poll();
    const interval = setInterval(poll, 30000);
    return () => clearInterval(interval);
  }, [apiBase, advisorToken]);

  useEffect(() => {
    if (bridgeStatus !== "Paper Bridge connected") return;
    void loadPaperOrders();
    const interval = setInterval(() => void loadPaperOrders(), 15000);
    return () => clearInterval(interval);
  }, [bridgeStatus, bridgeBase, bridgeToken, maxOptionsCapital]);

  async function acknowledgeAll() {
    try {
      await advisorRequest("/api/advisor/notifications/acknowledge-all", { method: "POST" });
      setNotifications([]);
      setShowNotifications(false);
    } catch { /* ignore */ }
  }

  async function acknowledgeOne(id: string) {
    try {
      await advisorRequest(`/api/advisor/notifications/${id}/acknowledge`, { method: "POST" });
      setNotifications((prev) => prev.filter((n) => n.id !== id));
    } catch { /* ignore */ }
  }

  async function openNotification(notification: BrainNotification) {
    const capital = Number(maxOptionsCapital);
    setShowNotifications(false);
    if (!capital || capital <= 0) {
      setScanError("Set your weekly options allocation before validating an alert.");
      document.getElementById("capital-allocation")?.scrollIntoView({ behavior: "smooth" });
      return;
    }
    setAlertDetailSymbol(notification.symbol);
    setAlertDetailOpen(true);
    await openStock(notification.symbol);
  }

  async function checkAdvisor(base: string) {
    try {
      const response = await fetch(`${base.replace(/\/$/, "")}/health/`);
      if (!response.ok) throw new Error("health check failed");
      setStatus("Advisor connected");
    } catch {
      setStatus("Advisor unavailable");
    }
  }

  async function connectBridge() {
    setBridgeLoading(true);
    const base = bridgeBase.replace(/\/$/, "");
    window.localStorage.setItem("thetaforge-bridge-base", base);
    window.localStorage.setItem("thetaforge-bridge-token", bridgeToken);
    const headers: HeadersInit = bridgeToken ? { "X-ThetaForge-Bridge-Token": bridgeToken } : {};
    try {
      const connection = await fetch(`${base}/connect`, { method: "POST", headers });
      if (!connection.ok) {
        const body = await connection.json().catch(() => ({}));
        throw new Error(body.detail || `Bridge returned ${connection.status}`);
      }
      const positionResponse = await fetch(`${base}/positions`, { headers });
      if (!positionResponse.ok) {
        const body = await positionResponse.json().catch(() => ({}));
        throw new Error(body.detail || `Positions returned ${positionResponse.status}`);
      }
      setPositions(await positionResponse.json());
      setBridgeStatus("Paper Bridge connected");
      await loadPaperOrders(base);
    } catch (bridgeError) {
      setBridgeStatus(bridgeError instanceof Error ? bridgeError.message : "Bridge unavailable");
      setPositions([]);
    } finally {
      setBridgeLoading(false);
    }
  }

  async function loadPaperOrders(explicitBase?: string) {
    const base = (explicitBase || bridgeBase).replace(/\/$/, "");
    const headers: HeadersInit = bridgeToken ? { "X-ThetaForge-Bridge-Token": bridgeToken } : {};
    const capital = Number(maxOptionsCapital);
    const query = capital > 0 ? `?capital_limit=${encodeURIComponent(capital)}` : "";
    try {
      const response = await fetch(`${base}/orders${query}`, { headers });
      if (!response.ok) return;
      const result = await response.json() as PaperOrderLedger;
      setPaperOrders(result.orders || []);
      setCapitalReserved(result.capital_reserved || 0);
      setCapitalRemaining(result.capital_remaining);
    } catch {
      // The Bridge may be restarting; the next poll reconciles the ledger.
    }
  }

  async function cancelPaperOrder(orderId: string) {
    const headers: HeadersInit = {};
    if (bridgeToken) headers["X-ThetaForge-Bridge-Token"] = bridgeToken;
    try {
      const response = await fetch(`${bridgeBase.replace(/\/$/, "")}/orders/${orderId}/cancel`, {
        method: "POST",
        headers,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || `Cancel returned ${response.status}`);
      await loadPaperOrders();
    } catch (cancelError) {
      setScanError(cancelError instanceof Error ? cancelError.message : "Paper order could not be cancelled");
    }
  }

  function saveCapitalLimit(value: string) {
    setMaxOptionsCapital(value);
    if (value) window.localStorage.setItem("thetaforge-max-options-capital", value);
    else window.localStorage.removeItem("thetaforge-max-options-capital");
  }

  function forgetSavedTokens() {
    window.localStorage.removeItem("thetaforge-advisor-token");
    window.localStorage.removeItem("thetaforge-bridge-token");
    setAdvisorToken("");
    setBridgeToken("");
  }

  async function analyze(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    window.localStorage.setItem("thetaforge-api-base", apiBase.replace(/\/$/, ""));
    window.localStorage.setItem("thetaforge-advisor-token", advisorToken);
    try {
      const response = await advisorRequest("/api/advisor/brain/analyze", {
        method: "POST",
        body: JSON.stringify({ symbol: symbol.trim().toUpperCase() }),
      });
      if (!response.ok) throw new Error(`Analysis service returned ${response.status}`);
      setAnalysis(await response.json());
      setStatus("Advisor connected");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to reach the local Brain");
      setStatus("Advisor not connected");
    } finally {
      setLoading(false);
    }
  }

  async function fetchAutomaticOpportunities(capital: number, tokenOverride?: string) {
    setScanLoading(true);
    setScanError("");
    setStatus("Advisor scanning market");
    try {
      let bridgeSymbols: string[] = [];
      if (bridgeStatus === "Paper Bridge connected") {
        const headers: HeadersInit = {};
        if (bridgeToken) headers["X-ThetaForge-Bridge-Token"] = bridgeToken;
        const scannerResponse = await fetch(`${bridgeBase.replace(/\/$/, "")}/scanner/universe`, { headers });
        if (scannerResponse.ok) bridgeSymbols = (await scannerResponse.json() as { symbols?: string[] }).symbols || [];
      }
      const response = await advisorRequest("/api/advisor/opportunities", {
        method: "POST",
        body: JSON.stringify({ capital, current_positions: positions, bridge_symbols: bridgeSymbols }),
      }, tokenOverride);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Opportunity service returned ${response.status}`);
      }
      const result = await response.json() as RecommendationResponse;
      setTopTrades(result);
      setSelectedStock("");
      setStockTrades(null);
      setStatus("Advisor connected");
    } catch (requestError) {
      setScanError(requestError instanceof Error ? requestError.message : "Unable to scan the opportunity universe");
      // A scan can fail because a public market-data source is slow while the
      // hosted Advisor itself remains healthy. Do not mislabel that as a
      // service outage; the detailed scan message is shown in the panel.
      setStatus("Advisor connected · scan needs retry");
    } finally {
      setScanLoading(false);
    }
  }

  async function scanTopTrades(event: FormEvent) {
    event.preventDefault();
    const capital = Number(maxOptionsCapital);
    if (!capital || capital <= 0) {
      setScanError("Set your weekly options allocation before scanning for trade candidates.");
      return;
    }
    await fetchAutomaticOpportunities(capital);
  }

  async function verifyWithIBKR(result: RecommendationResponse): Promise<RecommendationResponse> {
    const uniqueLegs = new Map<string, { symbol: string; expiry: string; strike: number; right: "C" | "P" }>();
    for (const trade of result.recommendations) for (const leg of trade.legs) {
      const right = leg.type === "CALL" ? "C" : "P";
      uniqueLegs.set(quoteKey(trade.symbol, leg.expiry, leg.strike, right), { symbol: trade.symbol, expiry: leg.expiry, strike: leg.strike, right });
    }
    const headers: HeadersInit = { "Content-Type": "application/json" };
    if (bridgeToken) headers["X-ThetaForge-Bridge-Token"] = bridgeToken;
    const response = await fetch(`${bridgeBase.replace(/\/$/, "")}/options/quotes`, {
      method: "POST", headers, body: JSON.stringify({ legs: [...uniqueLegs.values()] }),
    });
    if (!response.ok) throw new Error("IBKR live-quote verification was unavailable");
    const payload = await response.json() as { quotes: IBKRQuote[] };
    const quotes = new Map(payload.quotes.map((quote) => [quoteKey(quote.symbol, quote.expiry, quote.strike, quote.right), quote]));

    return {
      ...result,
      recommendations: result.recommendations.map((trade) => {
        const pricedLegs = trade.legs.map((leg) => quotes.get(quoteKey(trade.symbol, leg.expiry, leg.strike, leg.type === "CALL" ? "C" : "P")));
        if (pricedLegs.some((quote) => !quote?.executable)) {
          return { ...trade, pricing_status: "IBKR quote is delayed, frozen, or unavailable — not executable" };
        }
        if (trade.legs.length === 1 && trade.legs[0].action === "SELL") {
          const quote = pricedLegs[0]!;
          const credit = quote.bid!;
          if (trade.legs[0].type === "PUT") {
            const maxLoss = (trade.legs[0].strike - credit) * 100;
            return { ...trade, net_credit: credit, net_debit: 0, max_profit: credit * 100, max_loss: maxLoss, capital_required: maxLoss, breakeven: trade.legs[0].strike - credit, pricing_status: "IBKR live bid/ask verified" };
          }
          return { ...trade, net_credit: credit, net_debit: 0, max_profit: credit * 100, pricing_status: "IBKR live bid/ask verified — covered shares checked at submit" };
        }
        // A two-leg vertical can be repriced exactly from the current bid/ask.
        // More complex structures retain IBKR live leg quotes but are left for
        // TWS Performance Profile to calculate as a combo.
        if (trade.legs.length !== 2 || trade.legs[0].type !== trade.legs[1].type) {
          return { ...trade, pricing_status: "IBKR live option-leg quotes verified — review combo profile in TWS" };
        }
        const sellIndex = trade.legs.findIndex((leg) => leg.action === "SELL");
        const buyIndex = trade.legs.findIndex((leg) => leg.action === "BUY");
        if (sellIndex < 0 || buyIndex < 0) return { ...trade, pricing_status: "IBKR live option-leg quotes verified" };
        const sell = pricedLegs[sellIndex]!;
        const buy = pricedLegs[buyIndex]!;
        const net = sell.bid! - buy.ask!;
        const width = Math.abs(trade.legs[sellIndex].strike - trade.legs[buyIndex].strike);
        const right = trade.legs[sellIndex].type;
        if (net >= 0) {
          const breakeven = right === "CALL" ? trade.legs[sellIndex].strike + net : trade.legs[sellIndex].strike - net;
          return { ...trade, net_credit: net, net_debit: 0, max_profit: net * 100, max_loss: (width - net) * 100, capital_required: (width - net) * 100, breakeven, pricing_status: "IBKR live bid/ask verified" };
        }
        const debit = Math.abs(net);
        const longIndex = buyIndex;
        const breakeven = right === "CALL" ? trade.legs[longIndex].strike + debit : trade.legs[longIndex].strike - debit;
        return { ...trade, net_credit: 0, net_debit: debit, max_profit: (width - debit) * 100, max_loss: debit * 100, capital_required: debit * 100, breakeven, pricing_status: "IBKR live bid/ask verified" };
      }),
    };
  }

  async function openStock(symbolToOpen: string) {
    const capital = Number(maxOptionsCapital);
    if (!capital) return;
    setSelectedStock(symbolToOpen);
    setStockLoading(true);
    setStockTrades(null);
    setScanError("");
    try {
      const response = await advisorRequest("/api/advisor/recommend", {
        method: "POST",
        body: JSON.stringify({
          capital,
          buying_power: capital,
          risk_tolerance: "moderate",
          watchlist: [symbolToOpen],
          current_positions: positions,
          max_positions: Math.max(3, positions.length + 3),
          diversify_underlyings: false,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Trade detail returned ${response.status}`);
      }
      const result = await response.json() as RecommendationResponse;
      let detailedResult = result;
      if (bridgeStatus === "Paper Bridge connected" && result.recommendations.length > 0) {
        try {
          detailedResult = await verifyWithIBKR(result);
        } catch {
          detailedResult = { ...result, recommendations: result.recommendations.map((trade) => ({ ...trade, pricing_status: "External indicative quotes — connect IBKR Bridge to verify" })) };
        }
      } else {
        detailedResult = { ...result, recommendations: result.recommendations.map((trade) => ({ ...trade, pricing_status: "External indicative quotes — connect IBKR Bridge to verify" })) };
      }
      setStockTrades(detailedResult);
      setTopTrades((current) => current || detailedResult);
    } catch (requestError) {
      setScanError(requestError instanceof Error ? requestError.message : "Unable to load this stock's trade structures");
    } finally {
      setStockLoading(false);
    }
  }

  async function submitPaperTrade(trade: TradeRecommendation) {
    const capital = Number(maxOptionsCapital);
    if (!capital || !trade.pricing_status?.startsWith("IBKR live")) return;
    setSubmittingTrade(trade.id);
    setPaperOrderStatus((current) => ({ ...current, [trade.id]: "Submitting paper combo to IBKR…" }));
    const headers: HeadersInit = { "Content-Type": "application/json" };
    if (bridgeToken) headers["X-ThetaForge-Bridge-Token"] = bridgeToken;
    try {
      const response = await fetch(`${bridgeBase.replace(/\/$/, "")}/orders/submit-combo`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          legs: trade.legs.map((leg) => ({ symbol: trade.symbol, expiry: leg.expiry, strike: leg.strike, right: leg.type === "CALL" ? "C" : "P", action: leg.action })),
          quantity: trade.quantity,
          capital_limit: capital,
          recommendation_id: trade.id,
          strategy: trade.strategy,
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || `Paper order returned ${response.status}`);
      setPaperOrderStatus((current) => ({
        ...current,
        [trade.id]: `Paper order ${result.status || "submitted"} · limit ${result.limit_price} · ${dollars(result.capital_remaining)} weekly capital remaining`,
      }));
      await loadPaperOrders();
    } catch (submitError) {
      setPaperOrderStatus((current) => ({ ...current, [trade.id]: submitError instanceof Error ? submitError.message : "Paper order could not be submitted" }));
    } finally {
      setSubmittingTrade(null);
    }
  }

  return (
    <main>
      <nav>
        <div className="brand"><span>θ</span> ThetaForge <small>PERSONAL TERMINAL · {VERSION}</small></div>
        <div className="nav-right">
          <a className="terminal-link" href="https://jadax.github.io/ThetaForge/" target="_blank" rel="noreferrer">Public journal ↗</a>
          <div className="notif-bell" onClick={() => setShowNotifications(!showNotifications)}>
            🔔{notifications.length > 0 && <span className="notif-badge">{notifications.length}</span>}
          </div>
          <div className={`bridge ${status.includes("connected") ? "online" : ""}`}><i /> {status}</div>
        </div>
      </nav>
      {showNotifications && <section className="notif-panel"><div className="notif-header"><div><h3>Qualified market signals</h3><p>These passed the discovery threshold. Open one to run the final option-chain, portfolio, and IBKR execution checks; a signal alone is never a trade instruction.</p></div>{notifications.length > 0 && <button onClick={acknowledgeAll}>Acknowledge all</button>}</div>{notifications.length === 0 ? <p className="notif-empty">No new qualified signals.{scannerStatus && ` Last scan analyzed ${scannerStatus.symbols_scanned_last_run} symbols and found ${scannerStatus.symbols_with_trades} qualifying signals.`}</p> : notifications.map((n) => <div className="notif-card" key={n.id} role="button" tabIndex={0} onClick={() => openNotification(n)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") void openNotification(n); }}><div className="notif-symbol">{n.symbol}</div><div className="notif-score" data-direction={n.score >= 0 ? "bull" : "bear"}>{n.score >= 0 ? "+" : ""}{n.score.toFixed(0)}</div><div className="notif-body"><b>{signalLabel(n.signal)}</b> · {signalLabel(n.regime)} · {signalLabel(n.best_strategy)}<p>{n.strategy_reasoning}</p>{n.iv_rank != null && <p>IVR {Math.round(n.iv_rank)} · {n.iv_hv_signal ? signalLabel(n.iv_hv_signal) : "no vol data"}</p>}</div><button className="notif-open" type="button" onClick={(event) => { event.stopPropagation(); void openNotification(n); }}>Validate</button><button className="notif-ack" aria-label={`Acknowledge ${n.symbol} alert`} onClick={(event) => { event.stopPropagation(); void acknowledgeOne(n.id); }}>✓</button></div>)}</section>}
      {alertDetailOpen && <div className="trade-modal-backdrop" role="presentation" onClick={() => setAlertDetailOpen(false)}><section className="trade-modal" role="dialog" aria-modal="true" aria-labelledby="alert-trade-title" onClick={(event) => event.stopPropagation()}>
        <div className="trade-modal-head"><div><p className="eyebrow">FINAL ADVISOR VALIDATION</p><h2 id="alert-trade-title">{alertDetailSymbol} trade structures</h2></div><button type="button" aria-label="Close trade details" onClick={() => setAlertDetailOpen(false)}>×</button></div>
        {stockLoading ? <p className="modal-message">Running the full option-chain, quality-gate, portfolio, and IBKR quote checks…</p> : stockTrades?.recommendations.length ? <div className="modal-trades">{stockTrades.recommendations.slice(0, 3).map((trade) => <article key={`modal-${trade.id}`}>
          <div className="modal-trade-head"><div><small>{signalLabel(trade.strategy)}</small><h3>{trade.symbol} · {trade.composite_score.toFixed(0)}/100</h3></div><span className={`pricing-status ${trade.pricing_status?.startsWith("IBKR live") ? "live" : "indicative"}`}>{trade.pricing_status || "Indicative pricing"}</span></div>
          <div className="modal-metrics"><span><small>POP</small><b>{percent(trade.probability_of_profit)}</b></span><span><small>MAX LOSS</small><b>{dollars(trade.max_loss)}</b></span><span><small>MAX PROFIT</small><b>{dollars(trade.max_profit)}</b></span><span><small>CAPITAL</small><b>{dollars(trade.capital_required)}</b></span></div>
          <div className="trade-legs">{trade.legs.map((leg, index) => <span key={`modal-${trade.id}-${index}`} className={leg.action === "SELL" ? "sell" : "buy"}>{leg.action} {leg.type} {leg.strike} · {leg.expiry}</span>)}</div>
          <p>{trade.reasoning}</p>
          <div className="paper-trade-action">{trade.pricing_status?.startsWith("IBKR live") ? <button type="button" onClick={() => submitPaperTrade(trade)} disabled={submittingTrade === trade.id}>{submittingTrade === trade.id ? "Submitting paper order…" : "Send paper order to IBKR"}</button> : <span>Connect the Paper Bridge to verify live IBKR quotes before this can be submitted.</span>}{paperOrderStatus[trade.id] && <small>{paperOrderStatus[trade.id]}</small>}</div>
        </article>)}</div> : <div className="modal-no-trade"><b>No trade.</b><p>The alert candidate did not pass the final Advisor filters or did not produce a valid defined-risk structure. Do not place it.</p></div>}
      </section></div>}

      <section className="hero">
        <p className="eyebrow">OPTIONS INTELLIGENCE · PAPER FIRST</p>
        <h1>Trade decisions, <em>without the noise.</em></h1>
        <p className="subhead">Your Advisor scans the market and selects the top stocks for options trades. Use this field only when you want to inspect a specific symbol yourself.</p>
        <form onSubmit={analyze}>
          <label>Optional symbol inspection<input aria-label="Symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)} maxLength={8} /></label>
          <button disabled={loading}>{loading ? "Reading market…" : "Run Brain analysis"}</button>
        </form>
        <details>
          <summary>Advisor API address and token</summary>
          <input className="api" value={apiBase} onChange={(event) => setApiBase(event.target.value)} aria-label="Local Brain address" />
          <input className="api" type="password" value={advisorToken} onChange={(event) => setAdvisorToken(event.target.value)} aria-label="Advisor API token" placeholder="Advisor API token — saved on this device" />
          <p>Use your Render service URL here for live analysis. The token must match <code>ADVISOR_API_TOKEN</code> on that service. This terminal runs locally and is not deployed publicly, so both this and the Bridge token below are saved in this browser's local storage on this device rather than re-entered every session — use "Forget saved tokens" if you ever share this machine. Your IBKR paper-trading Bridge remains local to your trading computer.</p>
          <button type="button" onClick={forgetSavedTokens}>Forget saved tokens</button>
        </details>
        {!advisorToken && <p className="error">Enter your Advisor API token to load market analysis.</p>}
        {error && <p className="error">{error}. Check the Render Advisor URL, then try again.</p>}
      </section>

      {!analysis ? <section className="empty"><b>Ready when you are.</b><span>Enter a symbol to combine volatility, technical, flow, PCR, and dealer-positioning context.</span></section> : <>
        <section className="analysis-head">
          <div><p className="eyebrow">LATEST READ · {analysis.regime}</p><h2>{analysis.symbol} <span>${analysis.stock_price.toFixed(2)}</span></h2></div>
          <div className="signal"><small>COMPOSITE SIGNAL</small><strong>{signalLabel(analysis.overall_signal)}</strong><span>{analysis.overall_score > 0 ? "+" : ""}{analysis.overall_score} score · {analysis.confidence}% confidence</span></div>
        </section>
        <section className="grid">
          <article className="strategy"><p className="eyebrow">BEST FIT</p><h3>{signalLabel(analysis.best_strategy)}</h3><p>{analysis.best_strategy_reasoning}</p></article>
          <article><p className="eyebrow">ONE-MONTH IDEAS</p>{analysis.recommendations_1m.length ? analysis.recommendations_1m.map((idea) => <div className="idea" key={idea.strategy}><b>{signalLabel(idea.strategy)}</b><span>{idea.suitability}% fit · {idea.typical_dte}</span></div>) : <p>No strong monthly setup.</p>}</article>
          <article><p className="eyebrow">SIGNAL LEDGER</p>{analysis.all_signals.map((item) => <div className="ledger" key={item.source}><b>{item.source}</b><span>{signalLabel(item.signal)}</span><small>{item.confidence}%</small></div>)}</article>
        </section>
      </>}

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">AUTOMATIC PAPER-ONLY OPPORTUNITY SCAN</p><h2>Advisor-selected stocks to trade</h2><p>The Advisor chooses these stocks from its market scan, then analyzes their option chains. Click an Advisor-selected stock to review its best eligible trade structures.</p></div>
          {topTrades && <div className="market-chip"><small>MARKET</small><b>{signalLabel(topTrades.market_context.regime)}</b><span>VIX {topTrades.market_context.vix.toFixed(1)}</span></div>}
        </div>
        <form className="scan-form" onSubmit={scanTopTrades}><button disabled={scanLoading}>{scanLoading ? "Scanning the market…" : "Refresh Advisor scan"}</button></form>
        <p className="scan-note">Uses your weekly options allocation as the scan budget. Open a selected stock while the Paper Bridge is connected to reprice eligible verticals from IBKR live bid/ask quotes. Anything else remains indicative.</p>
        {scanError && <p className="error">{scanError}</p>}
        {topTrades?.recommendations.length ? <><p className="choose-stock">These stocks were selected by the Advisor. Click one to open up to three independently qualified trade structures for that stock.</p><div className="stock-list">{topTrades.recommendations.map((trade, index) => <button type="button" onClick={() => openStock(trade.symbol)} className={`stock-card ${selectedStock === trade.symbol ? "selected" : ""}`} key={trade.id}><small>ADVISOR PICK #{index + 1} · {signalLabel(trade.strategy)}</small><b>{trade.symbol}</b><span>${trade.underlying_price.toFixed(2)} · {trade.composite_score.toFixed(0)} score</span></button>)}</div></> : null}
        {topTrades && (topTrades.recommendations.length ? <div className="trade-list">{(stockTrades?.recommendations || topTrades.recommendations).slice(0, 3).map((trade, index) => <article className="trade-card" key={trade.id}>
          <div className="trade-rank">#{index + 1}</div>
          <div className="trade-title"><p className="eyebrow">{trade.symbol} · ${trade.underlying_price.toFixed(2)}</p><h3>{signalLabel(trade.strategy)}</h3>{trade.pricing_status && <span className={`pricing-status ${trade.pricing_status.startsWith("IBKR live") ? "live" : "indicative"}`}>{trade.pricing_status}</span>}<p>{trade.reasoning}</p></div>
          <div className="trade-metrics"><span><small>COMPOSITE</small><b>{trade.composite_score.toFixed(0)}/100</b></span><span><small>PROBABILITY OF PROFIT</small><b>{percent(trade.probability_of_profit)}</b></span><span className="loss"><small>MAX LOSS</small><b>{dollars(trade.max_loss)}</b></span><span className="profit"><small>MAX PROFIT</small><b>{dollars(trade.max_profit)}</b></span><span><small>CAPITAL REQUIRED</small><b>{dollars(trade.capital_required)}</b></span><span><small>{trade.net_credit > 0 ? "NET CREDIT" : "NET DEBIT"}</small><b>{dollars(trade.net_credit || trade.net_debit)}</b></span>{typeof trade.theoretical_edge_pct === "number" ? <span><small>THEO EDGE</small><b>{trade.theoretical_edge_pct.toFixed(1)}%</b></span> : null}</div>
          <div className="trade-insight">
            <div className="payoff-visual" aria-label={`Maximum loss ${dollars(trade.max_loss)} and maximum profit ${dollars(trade.max_profit)}`}><div className="payoff-caption"><span>MAX LOSS {dollars(trade.max_loss)}</span><span>MAX PROFIT {dollars(trade.max_profit)}</span></div><div className="payoff-track"><i className="loss-fill" style={{ width: `${Math.max(20, Math.min(50, (trade.max_loss / Math.max(trade.max_loss + trade.max_profit, 1)) * 100))}%` }} /><i className="profit-fill" /></div><p>Defined risk/reward · breakeven {dollars(trade.breakeven)}</p></div>
            <div className="signal-stack"><div><small>CONFIDENCE</small><b>{percent(trade.confidence_score)}</b></div><div className="confidence-meter"><i style={{ width: `${Math.min(100, Math.max(0, trade.confidence_score))}%` }} /></div><p>IV rank {percent(trade.iv_rank)} · VIX {trade.vix.toFixed(1)} · {signalLabel(trade.market_regime)}</p></div>
            <div className="return-stack"><small>RETURN ON CAPITAL</small><b>{percent(trade.return_on_capital_pct)}</b><span>{percent(trade.annualized_return_pct)} annualized</span></div>
          </div>
          <ExpectedMoveChart trade={trade} />
          <div className="trade-legs">{trade.legs.map((leg, legIndex) => <span key={`${trade.id}-${legIndex}`} className={leg.action === "SELL" ? "sell" : "buy"}>{leg.action} {leg.type} {leg.strike} · {leg.expiry}</span>)}</div>
          <details className="trade-plan"><summary>View entry and exit plan</summary><div><section><small>ENTRY</small>{Object.entries(trade.entry_rules).map(([key, value]) => <p key={key}><b>{signalLabel(key)}</b>{value}</p>)}</section><section><small>EXIT</small>{Object.entries(trade.exit_rules).map(([key, value]) => <p key={key}><b>{signalLabel(key)}</b>{value}</p>)}</section></div></details>
          <div className="paper-trade-action">{trade.pricing_status?.startsWith("IBKR live") && [1, 2, 4].includes(trade.legs.length) ? <button type="button" onClick={() => submitPaperTrade(trade)} disabled={submittingTrade === trade.id}>{submittingTrade === trade.id ? "Submitting paper order…" : "Send paper order to IBKR"}</button> : <span>Connect the Paper Bridge and open this stock to verify live IBKR quotes before paper execution.</span>}{paperOrderStatus[trade.id] && <small>{paperOrderStatus[trade.id]}</small>}</div>
          <p className="trade-risk">{trade.risk_warning}</p>
        </article>)}</div> : <div className="no-trades"><b>No defined-risk candidates passed the Advisor’s filters.</b><span>This is a valid outcome—avoid forcing a trade. Expand the scan universe or try again when market data changes.</span></div>)}
        {topTrades?.shortlisted_symbols?.length ? <p className="scan-warning">Screened {topTrades.universe_size} liquid and actively traded underlyings ({topTrades.active_discoveries || 0} live screener discoveries); full options analysis ran on {topTrades.shortlisted_symbols.join(", ")}.</p> : null}
        {topTrades?.warnings.length ? <p className="scan-warning">{topTrades.warnings.join(" · ")}</p> : null}
      </section>

      <section className="bridge-panel">
        <div><p className="eyebrow">LOCAL IBKR PAPER BRIDGE</p><h3>{bridgeStatus}</h3><p>Connect the dashboard to the Bridge running beside your paper TWS or IB Gateway session.</p></div>
        <div className="bridge-controls">
          <label>Bridge address<input className="api" value={bridgeBase} onChange={(event) => setBridgeBase(event.target.value)} aria-label="IBKR Bridge address" /></label>
          <label>Bridge token<input className="api" type="password" value={bridgeToken} onChange={(event) => setBridgeToken(event.target.value)} aria-label="IBKR Bridge token" placeholder="Saved on this device" /></label>
          <button type="button" onClick={connectBridge} disabled={bridgeLoading}>{bridgeLoading ? "Connecting…" : "Connect paper Bridge"}</button>
        </div>
        {positions.length > 0 && <div className="positions">{positions.map((position, index) => <span key={position.id || `${position.symbol}-${position.contract_type || "legacy"}-${position.strike || ""}-${position.expiry || ""}-${position.right || ""}-${index}`}><b>{position.symbol}</b> {position.contract_type === "OPT" ? `${position.right} ${position.strike} · ${position.expiry}` : "stock"} · {position.position} @ ${position.average_cost.toFixed(2)}</span>)}</div>}
      </section>
      <section className="capital-limit" id="capital-allocation">
        <div><p className="eyebrow">WEEKLY OPTIONS ALLOCATION</p><h3>Maximum options capital</h3><p>Your hard budget for the opportunity scan and paper orders. Open and filled orders reserve their live maximum loss for the current ISO week.</p>{bridgeStatus === "Paper Bridge connected" && <div className="capital-usage"><span><small>RESERVED</small><b>{dollars(capitalReserved)}</b></span><span><small>REMAINING</small><b>{capitalRemaining === null ? "Set a limit" : dollars(capitalRemaining)}</b></span></div>}</div>
        <label>USD<input className="capital-input" type="number" min="0" step="100" value={maxOptionsCapital} onChange={(event) => saveCapitalLimit(event.target.value)} placeholder="Set your weekly limit" aria-label="Maximum options capital in US dollars" /></label>
      </section>
      {bridgeStatus === "Paper Bridge connected" && <section className="order-activity">
        <div className="order-activity-head"><div><p className="eyebrow">IBKR PAPER ACTIVITY</p><h3>Orders and fills</h3></div><button type="button" onClick={() => loadPaperOrders()}>Refresh</button></div>
        {paperOrders.length === 0 ? <p className="order-empty">No paper orders have been submitted through this Bridge yet.</p> : <div className="order-table">
          {paperOrders.map((order) => <article key={order.id}>
            <div><b>{order.symbol}</b><span>{signalLabel(order.strategy || "advisor trade")} · {order.quantity} combo</span></div>
            <div><small>STATUS</small><b className={`order-status ${order.status.toLowerCase()}`}>{order.status}</b></div>
            <div><small>FILLED</small><b>{order.filled}/{order.quantity}</b></div>
            <div><small>LIMIT</small><b>{order.limit_price.toFixed(2)}</b></div>
            <div><small>CAPITAL RESERVED</small><b>{dollars(order.max_loss_total)}</b></div>
            <div><small>SUBMITTED</small><b>{new Date(order.submitted_at).toLocaleString()}</b></div>
            {!["Filled", "Cancelled", "ApiCancelled", "Inactive"].includes(order.status) && <button type="button" onClick={() => cancelPaperOrder(order.id)}>Cancel</button>}
          </article>)}
        </div>}
      </section>}
      <section className="safety"><b>Execution boundary</b><span>The Bridge is paper-only. Start the local Bridge and TWS/IB Gateway first; the dashboard can connect and control it but cannot start native applications.</span></section>
      <footer>Made with {"\u2665"} by <b>Tushant Sharma</b> · <span>Astraiva</span> · {VERSION}</footer>
    </main>
  );
}
