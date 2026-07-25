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

const signalLabel = (signal: string) => signal.replaceAll("_", " ");
const DEFAULT_ADVISOR_API = "https://thetaforge-production.up.railway.app";
const VERSION = "v0.4.0";

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

  useEffect(() => {
    const saved = window.localStorage.getItem("thetaforge-api-base");
    setApiBase(saved === "http://localhost:8000" || !saved ? DEFAULT_ADVISOR_API : saved);
    setBridgeBase(window.localStorage.getItem("thetaforge-bridge-base") || "http://127.0.0.1:8002");
    setBridgeToken(window.sessionStorage.getItem("thetaforge-bridge-token") || "");
  }, []);

  async function connectBridge() {
    setBridgeLoading(true);
    const base = bridgeBase.replace(/\/$/, "");
    window.localStorage.setItem("thetaforge-bridge-base", base);
    window.sessionStorage.setItem("thetaforge-bridge-token", bridgeToken);
    const headers: HeadersInit = bridgeToken ? { "X-ThetaForge-Bridge-Token": bridgeToken } : {};
    try {
      const connection = await fetch(`${base}/connect`, { method: "POST", headers });
      if (!connection.ok) throw new Error(`Bridge returned ${connection.status}`);
      const positionResponse = await fetch(`${base}/positions`, { headers });
      if (!positionResponse.ok) throw new Error(`Positions returned ${positionResponse.status}`);
      setPositions(await positionResponse.json());
      setBridgeStatus("Paper Bridge connected");
    } catch (bridgeError) {
      setBridgeStatus(bridgeError instanceof Error ? bridgeError.message : "Bridge unavailable");
      setPositions([]);
    } finally {
      setBridgeLoading(false);
    }
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

  return (
    <main>
      <nav>
        <div className="brand"><span>θ</span> ThetaForge <small>PERSONAL TERMINAL · {VERSION}</small></div>
        <div className={`bridge ${status.includes("connected") ? "online" : ""}`}><i /> {status}</div>
      </nav>

      <section className="hero">
        <p className="eyebrow">OPTIONS INTELLIGENCE · PAPER FIRST</p>
        <h1>Trade decisions, <em>without the noise.</em></h1>
        <p className="subhead">A focused decision desk for your IBKR workflow. Analysis stays deliberate; orders always remain yours to confirm.</p>
        <form onSubmit={analyze}>
          <label>Symbol<input aria-label="Symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)} maxLength={8} /></label>
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

      <section className="bridge-panel">
        <div><p className="eyebrow">LOCAL IBKR PAPER BRIDGE</p><h3>{bridgeStatus}</h3><p>Connect the dashboard to the Bridge running beside your paper TWS or IB Gateway session.</p></div>
        <div className="bridge-controls">
          <label>Bridge address<input className="api" value={bridgeBase} onChange={(event) => setBridgeBase(event.target.value)} aria-label="IBKR Bridge address" /></label>
          <label>Bridge token<input className="api" type="password" value={bridgeToken} onChange={(event) => setBridgeToken(event.target.value)} aria-label="IBKR Bridge token" placeholder="Current session only" /></label>
          <button type="button" onClick={connectBridge} disabled={bridgeLoading}>{bridgeLoading ? "Connecting…" : "Connect paper Bridge"}</button>
        </div>
        {positions.length > 0 && <div className="positions">{positions.map((position) => <span key={position.symbol}><b>{position.symbol}</b> {position.position} @ ${position.average_cost.toFixed(2)}</span>)}</div>}
      </section>
      <section className="safety"><b>Execution boundary</b><span>The Bridge is paper-only. Start the local Bridge and TWS/IB Gateway first; the dashboard can connect and control it but cannot start native applications.</span></section>
      <footer>Created with love {"\u2665"} by <b>Tushant Sharma</b> · <span>Astraiva</span> · {VERSION}</footer>
    </main>
  );
}
