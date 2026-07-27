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

type BridgePosition = { symbol: string; position: number; average_cost: number };

type TradeRecommendation = {
  id: string;
  symbol: string;
  strategy: string;
  underlying_price: number;
  legs: Array<{ action: string; strike: number; expiry: string; type: string }>;
  quantity: number;
  capital_required: number;
  max_loss: number;
  probability_of_profit: number;
  composite_score: number;
  confidence_score: number;
  reasoning: string;
  risk_warning: string;
  entry_rules: Record<string, string>;
  exit_rules: Record<string, string>;
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
const DEFAULT_ADVISOR_API = "https://thetaforge-production.up.railway.app";
const VERSION = "v0.4.2";

export default function Home() {
  const [symbol, setSymbol] = useState("SPY");
  const [apiBase, setApiBase] = useState(DEFAULT_ADVISOR_API);
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

  useEffect(() => {
    const saved = window.localStorage.getItem("thetaforge-api-base");
    const advisorBase = saved === "http://localhost:8000" || !saved ? DEFAULT_ADVISOR_API : saved;
    setApiBase(advisorBase);
    setBridgeBase(window.localStorage.getItem("thetaforge-bridge-base") || "http://127.0.0.1:8002");
    setBridgeToken(window.sessionStorage.getItem("thetaforge-bridge-token") || "");
    const savedCapital = window.localStorage.getItem("thetaforge-max-options-capital") || "";
    setMaxOptionsCapital(savedCapital);
    void checkAdvisor(advisorBase);
    if (Number(savedCapital) > 0) void fetchAutomaticOpportunities(Number(savedCapital));
  }, []);

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
    window.sessionStorage.setItem("thetaforge-bridge-token", bridgeToken);
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
    } catch (bridgeError) {
      setBridgeStatus(bridgeError instanceof Error ? bridgeError.message : "Bridge unavailable");
      setPositions([]);
    } finally {
      setBridgeLoading(false);
    }
  }

  function saveCapitalLimit(value: string) {
    setMaxOptionsCapital(value);
    if (value) window.localStorage.setItem("thetaforge-max-options-capital", value);
    else window.localStorage.removeItem("thetaforge-max-options-capital");
  }

  async function analyze(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    const base = apiBase.replace(/\/$/, "");
    window.localStorage.setItem("thetaforge-api-base", base);
    try {
      const response = await fetch(`${base}/api/advisor/brain/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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

  async function fetchAutomaticOpportunities(capital: number) {
    setScanLoading(true);
    setScanError("");
    setStatus("Advisor scanning market");
    const base = apiBase.replace(/\/$/, "");
    try {
      const response = await fetch(`${base}/api/advisor/opportunities`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ capital, current_positions: positions }),
      });
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

  async function openStock(symbolToOpen: string) {
    const capital = Number(maxOptionsCapital);
    if (!capital) return;
    setSelectedStock(symbolToOpen);
    setStockLoading(true);
    setScanError("");
    try {
      const response = await fetch(`${apiBase.replace(/\/$/, "")}/api/advisor/recommend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          capital,
          buying_power: capital,
          risk_tolerance: "moderate",
          watchlist: [symbolToOpen],
          current_positions: positions,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Trade detail returned ${response.status}`);
      }
      setStockTrades(await response.json());
    } catch (requestError) {
      setScanError(requestError instanceof Error ? requestError.message : "Unable to load this stock's trade structures");
    } finally {
      setStockLoading(false);
    }
  }

  return (
    <main>
      <nav>
        <div className="brand"><span>θ</span> ThetaForge <small>PERSONAL TERMINAL · {VERSION}</small></div>
        <div className={`bridge ${status.includes("connected") ? "online" : ""}`}><i /> {status}</div>
      </nav>

      <section className="hero">
        <p className="eyebrow">OPTIONS INTELLIGENCE · PAPER FIRST</p>
        <h1>Trade decisions, <em>without the noise.</em></h1>
        <p className="subhead">Your Advisor scans the market and selects the top stocks for options trades. Use this field only when you want to inspect a specific symbol yourself.</p>
        <form onSubmit={analyze}>
          <label>Optional symbol inspection<input aria-label="Symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)} maxLength={8} /></label>
          <button disabled={loading}>{loading ? "Reading market…" : "Run Brain analysis"}</button>
        </form>
        <details>
          <summary>Advisor API address</summary>
          <input className="api" value={apiBase} onChange={(event) => setApiBase(event.target.value)} aria-label="Local Brain address" />
          <p>Use your Railway service URL here for live analysis. Your IBKR paper-trading Bridge remains local to your trading computer.</p>
        </details>
        {error && <p className="error">{error}. Check the Railway Advisor URL, then try again.</p>}
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
        <p className="scan-note">Uses your weekly options allocation as the scan budget. Review every candidate before staging a paper order.</p>
        {scanError && <p className="error">{scanError}</p>}
        {topTrades?.recommendations.length ? <><p className="choose-stock">These stocks were selected by the Advisor. Click one to open its filtered trade structures.</p><div className="stock-list">{topTrades.recommendations.map((trade, index) => <button type="button" onClick={() => openStock(trade.symbol)} className={`stock-card ${selectedStock === trade.symbol ? "selected" : ""}`} key={trade.id}><small>ADVISOR PICK #{index + 1} · {signalLabel(trade.strategy)}</small><b>{trade.symbol}</b><span>${trade.underlying_price.toFixed(2)} · {trade.composite_score.toFixed(0)} score</span></button>)}</div></> : null}
        {topTrades && (topTrades.recommendations.length ? <div className="trade-list">{(stockTrades?.recommendations || topTrades.recommendations).slice(0, 3).map((trade, index) => <article className="trade-card" key={trade.id}>
          <div className="trade-rank">#{index + 1}</div>
          <div className="trade-title"><p className="eyebrow">{trade.symbol} · ${trade.underlying_price.toFixed(2)}</p><h3>{signalLabel(trade.strategy)}</h3><p>{trade.reasoning}</p></div>
          <div className="trade-metrics"><span><small>COMPOSITE</small><b>{trade.composite_score.toFixed(0)}</b></span><span><small>POP</small><b>{trade.probability_of_profit.toFixed(0)}%</b></span><span><small>AT RISK</small><b>${trade.max_loss.toFixed(0)}</b></span><span><small>CAPITAL</small><b>${trade.capital_required.toFixed(0)}</b></span></div>
          <div className="trade-legs">{trade.legs.map((leg, legIndex) => <span key={`${trade.id}-${legIndex}`} className={leg.action === "SELL" ? "sell" : "buy"}>{leg.action} {leg.type} {leg.strike} · {leg.expiry}</span>)}</div>
          <p className="trade-risk">{trade.risk_warning}</p>
        </article>)}</div> : <div className="no-trades"><b>No defined-risk candidates passed the Advisor’s filters.</b><span>This is a valid outcome—avoid forcing a trade. Expand the scan universe or try again when market data changes.</span></div>)}
        {topTrades?.shortlisted_symbols?.length ? <p className="scan-warning">Screened {topTrades.universe_size} liquid and actively traded underlyings ({topTrades.active_discoveries || 0} live screener discoveries); full options analysis ran on {topTrades.shortlisted_symbols.join(", ")}.</p> : null}
        {topTrades?.warnings.length ? <p className="scan-warning">{topTrades.warnings.join(" · ")}</p> : null}
      </section>

      <section className="bridge-panel">
        <div><p className="eyebrow">LOCAL IBKR PAPER BRIDGE</p><h3>{bridgeStatus}</h3><p>Connect the dashboard to the Bridge running beside your paper TWS or IB Gateway session.</p></div>
        <div className="bridge-controls">
          <label>Bridge address<input className="api" value={bridgeBase} onChange={(event) => setBridgeBase(event.target.value)} aria-label="IBKR Bridge address" /></label>
          <label>Bridge token<input className="api" type="password" value={bridgeToken} onChange={(event) => setBridgeToken(event.target.value)} aria-label="IBKR Bridge token" placeholder="Current session only" /></label>
          <button type="button" onClick={connectBridge} disabled={bridgeLoading}>{bridgeLoading ? "Connecting…" : "Connect paper Bridge"}</button>
        </div>
        {positions.length > 0 && <div className="positions">{positions.map((position) => <span key={position.symbol}><b>{position.symbol}</b> {position.position} @ ${position.average_cost.toFixed(2)}</span>)}</div>}
      </section>
      <section className="capital-limit">
        <div><p className="eyebrow">WEEKLY OPTIONS ALLOCATION</p><h3>Maximum options capital</h3><p>Your hard budget for the opportunity scan. The Advisor may use less, never more. Update this whenever your weekly allocation changes.</p></div>
        <label>USD<input className="capital-input" type="number" min="0" step="100" value={maxOptionsCapital} onChange={(event) => saveCapitalLimit(event.target.value)} placeholder="Set your weekly limit" aria-label="Maximum options capital in US dollars" /></label>
      </section>
      <section className="safety"><b>Execution boundary</b><span>The Bridge is paper-only. Start the local Bridge and TWS/IB Gateway first; the dashboard can connect and control it but cannot start native applications.</span></section>
      <footer>Made with {"\u2665"} by <b>Tushant Sharma</b> · <span>Astraiva</span> · {VERSION}</footer>
    </main>
  );
}
