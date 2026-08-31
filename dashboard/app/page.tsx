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

type ScanEntry = {
  score: number;
  signal: string;
  regime?: string;
  strategy: string;
  iv_rank?: number | null;
  iv_percentile?: number | null;
  eff_iv_rank?: number | null;
  iv_hv_signal?: string | null;
  rv_band?: string | null;
  expected_move_pct?: number | null;
  term_structure?: string | null;
  flow_bias?: string | null;
  pcr_signal?: string | null;
  gex_regime?: string | null;
  vol_risk_premium?: number | null;
};

type ScannerStatus = {
  is_running?: boolean;
  market_open?: boolean;
  last_run: string | null;
  next_run?: string | null;
  interval_seconds?: number;
  symbols_scanned_last_run: number;
  symbols_with_trades: number;
  pending_notifications?: number;
  total_notifications?: number;
  last_results?: { symbols?: Record<string, ScanEntry>; last_full_run?: string };
};

type WatchlistItem = {
  symbol: string;
  added_at: string;
  notes: string;
  tags: string[];
  custom_delta: number;
  custom_dte: number;
  custom_strategies: string[];
};

type DashboardTopPick = { symbol: string; signal: string; score: number };

type DashboardResult = {
  vix: number;
  regime: string;
  account: {
    equity: number;
    buying_power: number;
    capital_deployed: number;
    capital_deployed_pct: number;
    num_positions: number;
  };
  portfolio_risk: { net_delta: number; net_vega: number; within_limits: boolean };
  watchlist_rankings: Analysis[];
  top_picks_1w: DashboardTopPick[];
  top_picks_1m: DashboardTopPick[];
};

type AssetRead = {
  label: string;
  level?: number;
  level_pct?: number;
  change_bp?: number;
  change_1d_pct?: number;
  change_5d_pct?: number | null;
  trend?: string;
};

type MarketSymbolRead = {
  symbol: string;
  price: number;
  change_1d_pct: number;
  change_1m_pct: number;
  above_200d: boolean;
  rsi_14: number;
  adx: number;
  macd_bullish: boolean;
  volume_ratio: number | null;
  percent_off_52w_high: number;
  volatility_20d_annualized: number | null;
  read: "bullish" | "bearish" | "neutral";
};

type MarketOverview = {
  overview: {
    as_of: string;
    indices: Record<string, AssetRead>;
    bonds: Record<string, AssetRead>;
    commodities: Record<string, AssetRead>;
    sectors: Record<string, number>;
    yield_curve: { short: number | null; mid: number | null; long: number | null; very_long: number | null; shape: string | null };
    risk_tilt: { tilt: "risk_on" | "risk_off" | "mixed"; indices_up: number; indices_down: number };
  };
  symbols: Record<string, MarketSymbolRead>;
};

type ManagementPosition = {
  symbol: string;
  strategy?: string;
  short_strike: number;
  long_strike: number;
  expiry?: string;
  credit_received?: number;
  quantity?: number;
  spot?: number;
  dte?: number;
  short_leg_value?: number;
  days_to_earnings?: number;
  capital_required?: number;
};

type ManagementAction = {
  action: string;
  reason: string;
  urgency: "low" | "medium" | "high";
  symbol: string;
  strategy: string;
  profit_pct: number | null;
  loss_to_credit: number | null;
  dte: number | null;
  short_strike: number;
  long_strike: number;
  max_loss: number | null;
  max_profit: number;
  spot?: number | null;
  short_leg_value?: number | null;
};

type PortfolioPlan = {
  can_open_new: boolean;
  violations: string[];
  num_positions: number;
  max_positions: number;
  per_symbol_capital: Record<string, number>;
  max_symbol_slice: number;
  realized_drawdown_pct: number;
  drawdown_breaker_pct: number;
};

type ManagementResponse = {
  actions: ManagementAction[];
  portfolio: PortfolioPlan;
};

type EquityRecommendation = {
  id: string;
  symbol: string;
  shares: number;
  entry_price: number;
  stop_price: number;
  target_price: number | null;
  risk_per_share: number;
  max_loss_total: number;
  strategy: string;
  score: number;
  gate: string | null;
  gate_reason?: string | null;
  rationale: string;
  atr_14: number;
  rsi_14: number;
  adx_14: number;
  trend: string;
  read: string;
  timestamp: string;
};

type EquityNotification = {
  id: string;
  symbol: string;
  score: number;
  signal: string;
  read: string;
  trend: string;
  timestamp: string;
  acknowledged: boolean;
};

type EquityScannerStatus = {
  last_run: string | null;
  symbols_scanned_last_run: number;
  scans_completed: number;
  total_evaluations: number;
  passes: number;
  pending_count: number;
  market_open: boolean;
  last_error: string | null;
};

type EquityManagementPosition = {
  symbol: string;
  entry_price: number;
  stop_price: number;
  target_price?: number | null;
  highest_high?: number;
  risk_per_share?: number;
  shares?: number;
  opened_at?: string;
};

type EquityManagementAction = {
  action: string;
  reason: string;
  symbol: string;
  shares: number;
  current_price: number | null;
  highest_high: number | null;
};

type EquityManagementResponse = {
  actions: EquityManagementAction[];
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

type PnLLeg = {
  action: "BUY" | "SELL";
  option_type: "call" | "put";
  strike: string;
  entry_price: string;
};

type PnLResult = {
  spot: number;
  contracts: number;
  net_entry_per_share: number;
  net_entry: number;
  max_profit: number;
  max_loss: number;
  risk_reward: number;
  breakevens: number[];
  pop_at_expiry: number | null;
  pnl_points: Array<{ spot: number; pnl: number }>;
  error?: string;
};

type GexHeatmapResult = {
  underlying: number;
  total_call_gex: number;
  total_put_gex: number;
  net_gex: number;
  dealer_gex: number;
  gex_regime: string;
  zero_gamma_strike: number | null;
  strike_gex: Record<string, number>;
  error?: string;
};

type PlaybookSummary = {
  id: string;
  name: string;
  strategy_type: string;
  risk_profile: string;
  premium_printer: boolean;
};

type PlaybookDetail = PlaybookSummary & {
  mechanics: string;
  entry_rules: string;
  management: string;
  common_mistakes: string;
  best_for: string;
  risk_warning: string;
  error?: string;
  found?: boolean;
};

type ChainSide = {
  bid: number;
  ask: number;
  mid: number;
  last: number;
  iv: number;
  open_interest: number;
  volume: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
};

type ChainRow = {
  strike: number;
  call?: ChainSide;
  put?: ChainSide;
  put_call_oi_ratio?: number;
};

type ChainExpiry = { expiry: string; dte: number };

type ChainResult = {
  underlying: number;
  expiry: string;
  dte: number;
  expiries: ChainExpiry[];
  table: ChainRow[];
  summary: {
    dte: number;
    atm_iv: number;
    atm_straddle_mid: number;
    expected_move_pct: number;
    expected_move_1sd: number;
    expected_move_low: number;
    expected_move_high: number;
    max_pain_strike: number | null;
    call_wall?: number;
    put_floor?: number;
    call_oi_total: number;
    put_oi_total: number;
    put_call_oi_ratio: number | null;
    call_volume_total: number;
    put_volume_total: number;
    put_call_volume_ratio: number | null;
    iv_rank?: number;
    iv_percentile?: number;
    hv_20?: number;
    nvrp?: number;
    nvrp_regime?: string;
    iv_skew?: { expiry: string; atm_iv: number; rr25: number; bf25: number; rr25_norm: number; bf25_norm: number; regime: string; reasoning: string };
  };
  error?: string;
};

type AlertRule = {
  rule_id: string;
  symbol: string;
  alert_type: string;
  threshold: number | string;
  priority: string;
  message: string;
  triggered: boolean;
  created_at: string;
  one_time: boolean;
};

type AlertTemplate = {
  template_id: string;
  name: string;
  alert_type: string;
  default_threshold: number;
  priority: string;
  description: string;
};

type AlertHistoryEvent = {
  rule_id: string;
  symbol: string;
  alert_type: string;
  priority: string;
  message: string;
  current_value: number;
  threshold: number | string;
  timestamp: string;
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
// At-expiry P/L curve for a multi-leg structure (OptionStrat / Market
// Chameleon pattern). Pure client-side drawing over the calculator's points.
function PnLCurve({ result }: { result: PnLResult }) {
  const points = result.pnl_points || [];
  if (points.length < 2) return <div className="em-chart em-chart-empty">Not enough price points to draw the curve.</div>;
  const W = 560;
  const H = 120;
  const PADX = 10;
  const PADY = 8;
  const xs = points.map((point) => point.spot);
  const ys = points.map((point) => point.pnl);
  const loX = Math.min(...xs);
  const hiX = Math.max(...xs);
  const loY = Math.min(0, ...ys);
  const hiY = Math.max(0, ...ys);
  const spanX = Math.max(hiX - loX, 1e-6);
  const spanY = Math.max(hiY - loY, 1e-6);
  const x = (value: number) => PADX + ((value - loX) / spanX) * (W - PADX * 2);
  const y = (value: number) => PADY + (1 - (value - loY) / spanY) * (H - PADY * 2);
  const path = points.map((point, index) => `${index === 0 ? "M" : "L"}${x(point.spot).toFixed(1)},${y(point.pnl).toFixed(1)}`).join(" ");
  const zeroY = y(0);
  const fillPath = `${path} L${x(hiX)},${zeroY.toFixed(1)} L${x(loX)},${zeroY.toFixed(1)} Z`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="pnl-svg" role="img" aria-label="At-expiry P/L curve">
      <defs>
        <linearGradient id="pnlFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#c9ff5d" stopOpacity="0.26" />
          <stop offset="100%" stopColor="#c9ff5d" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <line x1={PADX} y1={zeroY} x2={W - PADX} y2={zeroY} stroke="#4c6754" strokeWidth="1" strokeDasharray="3 3" />
      <path d={fillPath} fill="url(#pnlFill)" />
      <path d={path} fill="none" stroke="#c9ff5d" strokeWidth="1.8" />
      {(result.breakevens || []).map((be, index) => (
        <line key={index} x1={x(be)} y1={PADY} x2={x(be)} y2={H - PADY} stroke="#f0c982" strokeWidth="1" strokeDasharray="2 3" />
      ))}
    </svg>
  );
}

const gexHeatBucket = (value: number) => {
  const magnitude = Math.abs(value);
  if (magnitude >= 50) return "extreme";
  if (magnitude >= 20) return "hot";
  if (magnitude >= 5) return "elevated";
  if (magnitude > 0) return "normal";
  return "flat";
};

const quoteKey = (symbol: string, expiry: string, strike: number, right: string) => `${symbol}|${expiry}|${strike}|${right}`;
const DEFAULT_ADVISOR_API = "https://thetaforge-advisor.onrender.com";
const NON_ACTIONABLE_STRATEGIES = new Set(["no_trade", "avoid_new_positions", "roll_or_close"]);
const ALERT_SCORE_FLOOR = 75;
const VERSION = "v1.17.16";

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
  const [markets, setMarkets] = useState<MarketOverview | null>(null);
  const [marketsLoading, setMarketsLoading] = useState(false);
  const [marketsError, setMarketsError] = useState("");
  const [marketsSymbols, setMarketsSymbols] = useState("");
  const [managementPositions, setManagementPositions] = useState("");
  const [managementCapital, setManagementCapital] = useState("");
  const [managementRealized, setManagementRealized] = useState("");
  const [managementResult, setManagementResult] = useState<ManagementResponse | null>(null);
  const [managementLoading, setManagementLoading] = useState(false);
  const [managementError, setManagementError] = useState("");

  const [equityNotifications, setEquityNotifications] = useState<EquityNotification[]>([]);
  const [equityScannerStatus, setEquityScannerStatus] = useState<EquityScannerStatus | null>(null);
  const [equityRecs, setEquityRecs] = useState<Record<string, EquityRecommendation>>({});
  const [equityRecError, setEquityRecError] = useState("");
  const [equityRecLoading, setEquityRecLoading] = useState("");
  const [equityPositions, setEquityPositions] = useState("");
  const [equityManagementCapital, setEquityManagementCapital] = useState("5000");
  const [equityManagementResult, setEquityManagementResult] = useState<EquityManagementResponse | null>(null);
  const [equityManagementLoading, setEquityManagementLoading] = useState(false);
  const [equityManagementError, setEquityManagementError] = useState("");

  const [pnlLegs, setPnlLegs] = useState<PnLLeg[]>([
    { action: "SELL", option_type: "put", strike: "45", entry_price: "1.50" },
    { action: "BUY", option_type: "put", strike: "40", entry_price: "0.60" },
  ]);
  const [pnlSpot, setPnlSpot] = useState("50");
  const [pnlContracts, setPnlContracts] = useState("1");
  const [pnlIv, setPnlIv] = useState("0.30");
  const [pnlDte, setPnlDte] = useState("30");
  const [pnlResult, setPnlResult] = useState<PnLResult | null>(null);
  const [pnlLoading, setPnlLoading] = useState(false);
  const [pnlError, setPnlError] = useState("");

  const [gexSymbol, setGexSymbol] = useState("");
  const [gexResult, setGexResult] = useState<GexHeatmapResult | null>(null);
  const [gexLoading, setGexLoading] = useState(false);
  const [gexError, setGexError] = useState("");

  const [playbooks, setPlaybooks] = useState<PlaybookSummary[]>([]);
  const [playbookDetail, setPlaybookDetail] = useState<Record<string, PlaybookDetail>>({});
  const [playbookOpen, setPlaybookOpen] = useState<string | null>(null);
  const [playbookLoading, setPlaybookLoading] = useState("");
  const [playbookError, setPlaybookError] = useState("");

  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookSaving, setWebhookSaving] = useState(false);
  const [webhookStatus, setWebhookStatus] = useState("");

  const [chainSymbol, setChainSymbol] = useState("");
  const [chainTargetDte, setChainTargetDte] = useState("30");
  const [chainResult, setChainResult] = useState<ChainResult | null>(null);
  const [chainLoading, setChainLoading] = useState(false);
  const [chainError, setChainError] = useState("");

  const [alertRules, setAlertRules] = useState<AlertRule[]>([]);
  const [alertTemplates, setAlertTemplates] = useState<AlertTemplate[]>([]);
  const [alertHistory, setAlertHistory] = useState<AlertHistoryEvent[]>([]);
  const [alertGallerySymbol, setAlertGallerySymbol] = useState("");
  const [alertGalleryThreshold, setAlertGalleryThreshold] = useState("");
  const [alertCenterError, setAlertCenterError] = useState("");

  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [newWatchSymbol, setNewWatchSymbol] = useState("");
  const [watchlistMsg, setWatchlistMsg] = useState("");
  const [watchlistError, setWatchlistError] = useState("");
  const [watchlistRankings, setWatchlistRankings] = useState<Analysis[]>([]);
  const [watchlistAnalyzing, setWatchlistAnalyzing] = useState(false);

  const [commandCenterResult, setCommandCenterResult] = useState<DashboardResult | null>(null);
  const [commandCapital, setCommandCapital] = useState("100000");
  const [commandBuyingPower, setCommandBuyingPower] = useState("50000");
  const [commandPositions, setCommandPositions] = useState("");
  const [commandLoading, setCommandLoading] = useState(false);
  const [commandError, setCommandError] = useState("");

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
        const equityResponse = await advisorRequest("/api/advisor/equity/notifications?unacknowledged_only=true&limit=20");
        if (equityResponse.ok) {
          const data = await equityResponse.json() as { notifications: EquityNotification[] };
          setEquityNotifications(data.notifications || []);
        }
        const equityStatusResponse = await advisorRequest("/api/advisor/equity/scanner/status");
        if (equityStatusResponse.ok) setEquityScannerStatus(await equityStatusResponse.json() as EquityScannerStatus);
      } catch { /* backend may be down */ }
    };
    void poll();
    const interval = setInterval(poll, 30000);
    return () => clearInterval(interval);
  }, [apiBase, advisorToken]);

  useEffect(() => {
    if (!apiBase || !advisorToken) return;
    void loadPlaybooks();
    void loadWebhook();
    void loadAlerts();
    void loadWatchlist();
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

  async function loadMarkets(event: FormEvent) {
    event.preventDefault();
    setMarketsLoading(true);
    setMarketsError("");
    setStatus("Reading the market map");
    try {
      const symbols = marketsSymbols.split(",").map((value) => value.trim().toUpperCase()).filter(Boolean);
      const response = await advisorRequest("/api/advisor/markets", {
        method: "POST",
        body: JSON.stringify({ symbols }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Market map returned ${response.status}`);
      }
      setMarkets(await response.json());
      setStatus("Advisor connected");
    } catch (requestError) {
      setMarketsError(requestError instanceof Error ? requestError.message : "Unable to load the market map");
      setStatus("Advisor connected · markets unavailable");
    } finally {
      setMarketsLoading(false);
    }
  }

  async function loadManagement(event: FormEvent) {
    event.preventDefault();
    setManagementLoading(true);
    setManagementError("");
    setStatus("Evaluating open positions");
    try {
      let positions: ManagementPosition[];
      try {
        positions = JSON.parse(managementPositions || "[]") as ManagementPosition[];
      } catch {
        throw new Error("Positions must be valid JSON, e.g. [{\"symbol\":\"AAPL\",\"strategy\":\"bull_put\",\"short_strike\":200,\"long_strike\":190,\"credit_received\":1.2,\"dte\":30}]");
      }
      if (!Array.isArray(positions) || positions.length === 0) throw new Error("Enter at least one open position.");
      const response = await advisorRequest("/api/advisor/positions/management", {
        method: "POST",
        body: JSON.stringify({
          positions,
          capital: managementCapital ? Number(managementCapital) : 100000,
          realized_pnl: managementRealized ? Number(managementRealized) : 0,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Position management returned ${response.status}`);
      }
      setManagementResult(await response.json());
      setStatus("Advisor connected");
    } catch (requestError) {
      setManagementError(requestError instanceof Error ? requestError.message : "Unable to evaluate positions");
      setStatus("Advisor connected · management unavailable");
    } finally {
      setManagementLoading(false);
    }
  }

  async function loadEquityRecommendation(notification: EquityNotification) {
    setEquityRecLoading(notification.symbol);
    setEquityRecError("");
    setStatus(`Scoring ${notification.symbol} for a momentum long`);
    try {
      const response = await advisorRequest("/api/advisor/equity/recommend", {
        method: "POST",
        body: JSON.stringify({
          symbol: notification.symbol,
          capital: 5000,
          current_positions: [],
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Equity recommendation returned ${response.status}`);
      }
      const data = await response.json() as { recommendation: EquityRecommendation };
      setEquityRecs((prev) => ({ ...prev, [notification.symbol]: data.recommendation }));
      setStatus("Advisor connected");
    } catch (requestError) {
      setEquityRecError(requestError instanceof Error ? requestError.message : "Unable to score the equity setup");
      setStatus("Advisor connected · equity scoring unavailable");
    } finally {
      setEquityRecLoading("");
    }
  }

  async function acknowledgeEquity(id: string) {
    try {
      await advisorRequest(`/api/advisor/equity/notifications/${id}/acknowledge`, { method: "POST" });
      setEquityNotifications((prev) => prev.filter((n) => n.id !== id));
    } catch { /* ignore */ }
  }

  async function loadEquityManagement(event: FormEvent) {
    event.preventDefault();
    setEquityManagementLoading(true);
    setEquityManagementError("");
    setStatus("Evaluating open equity positions");
    try {
      let positions: EquityManagementPosition[];
      try {
        positions = JSON.parse(equityPositions || "[]") as EquityManagementPosition[];
      } catch {
        throw new Error("Positions must be valid JSON, e.g. [{\"symbol\":\"AAPL\",\"entry_price\":210.00,\"stop_price\":196.00,\"target_price\":225.00,\"highest_high\":212.50,\"shares\":10}]");
      }
      if (!Array.isArray(positions) || positions.length === 0) throw new Error("Enter at least one open position.");
      const response = await advisorRequest("/api/advisor/equity/positions/management", {
        method: "POST",
        body: JSON.stringify({
          positions,
          capital: equityManagementCapital ? Number(equityManagementCapital) : 5000,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Equity management returned ${response.status}`);
      }
      setEquityManagementResult(await response.json());
      setStatus("Advisor connected");
    } catch (requestError) {
      setEquityManagementError(requestError instanceof Error ? requestError.message : "Unable to evaluate equity positions");
      setStatus("Advisor connected · equity management unavailable");
    } finally {
      setEquityManagementLoading(false);
    }
  }

  function updatePnlLeg(index: number, field: keyof PnLLeg, value: string) {
    setPnlLegs((prev) => prev.map((leg, i) => (i === index ? { ...leg, [field]: value as never } : leg)));
  }

  function addPnlLeg() {
    setPnlLegs((prev) => [...prev, { action: "BUY", option_type: "put", strike: "", entry_price: "" }]);
  }

  function removePnlLeg(index: number) {
    setPnlLegs((prev) => prev.filter((_, i) => i !== index));
  }

  async function calculatePnl(event: FormEvent) {
    event.preventDefault();
    setPnlLoading(true);
    setPnlError("");
    const legs = pnlLegs
      .map((leg) => ({ action: leg.action, option_type: leg.option_type, strike: Number(leg.strike), entry_price: Number(leg.entry_price) }))
      .filter((leg) => leg.strike > 0);
    if (legs.length === 0) {
      setPnlError("Add at least one leg with a strike and entry premium.");
      setPnlLoading(false);
      return;
    }
    try {
      const response = await advisorRequest("/api/advisor/analytics/pnl-calculator", {
        method: "POST",
        body: JSON.stringify({
          legs,
          spot: Number(pnlSpot),
          contracts: Number(pnlContracts) || 1,
          iv: pnlIv ? Number(pnlIv) : null,
          dte: pnlDte ? Number(pnlDte) : null,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `P/L calculator returned ${response.status}`);
      }
      setPnlResult(await response.json());
    } catch (requestError) {
      setPnlError(requestError instanceof Error ? requestError.message : "Unable to run the P/L calculator");
    } finally {
      setPnlLoading(false);
    }
  }

  async function loadGex(event: FormEvent) {
    event.preventDefault();
    setGexLoading(true);
    setGexError("");
    if (!gexSymbol.trim()) {
      setGexError("Enter a symbol first.");
      setGexLoading(false);
      return;
    }
    try {
      const response = await advisorRequest("/api/advisor/analytics/gex-heatmap", {
        method: "POST",
        body: JSON.stringify({ symbol: gexSymbol.trim().toUpperCase() }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `GEX heatmap returned ${response.status}`);
      }
      setGexResult(await response.json());
    } catch (requestError) {
      setGexError(requestError instanceof Error ? requestError.message : "Unable to load the GEX heatmap");
    } finally {
      setGexLoading(false);
    }
  }

  async function loadPlaybooks() {
    try {
      const response = await advisorRequest("/api/advisor/playbooks");
      if (!response.ok) return;
      const data = await response.json() as { playbooks: PlaybookSummary[] };
      setPlaybooks(data.playbooks || []);
    } catch { /* backend may be down */ }
  }

  async function openPlaybook(id: string) {
    setPlaybookOpen(id);
    setPlaybookError("");
    if (playbookDetail[id]) return;
    setPlaybookLoading(id);
    try {
      const response = await advisorRequest(`/api/advisor/playbooks/${id}`);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Playbook returned ${response.status}`);
      }
      const detail = await response.json() as PlaybookDetail;
      setPlaybookDetail((prev) => ({ ...prev, [id]: detail }));
    } catch (requestError) {
      setPlaybookError(requestError instanceof Error ? requestError.message : "Unable to load the playbook");
    } finally {
      setPlaybookLoading("");
    }
  }

  async function loadWebhook() {
    try {
      const response = await advisorRequest("/api/advisor/alerts/notify");
      if (!response.ok) return;
      const config = await response.json() as { configured: boolean; url: string };
      setWebhookUrl(config.url || "");
    } catch { /* backend may be down */ }
  }

  async function saveWebhook(event: FormEvent) {
    event.preventDefault();
    setWebhookSaving(true);
    setWebhookStatus("");
    if (!webhookUrl.trim().startsWith("https://")) {
      setWebhookStatus("Webhook URL must start with https://.");
      setWebhookSaving(false);
      return;
    }
    try {
      const response = await advisorRequest("/api/advisor/alerts/notify", {
        method: "POST",
        body: JSON.stringify({ url: webhookUrl.trim() }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Webhook returned ${response.status}`);
      }
      setWebhookStatus("Webhook saved — triggered alerts will be posted here.");
    } catch (requestError) {
      setWebhookStatus(requestError instanceof Error ? requestError.message : "Unable to save the webhook");
    } finally {
      setWebhookSaving(false);
    }
  }

  async function clearWebhook() {
    setWebhookSaving(true);
    try {
      const response = await advisorRequest("/api/advisor/alerts/notify", { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Webhook returned ${response.status}`);
      }
      setWebhookUrl("");
      setWebhookStatus("Webhook disabled.");
    } catch (requestError) {
      setWebhookStatus(requestError instanceof Error ? requestError.message : "Unable to clear the webhook");
    } finally {
      setWebhookSaving(false);
    }
  }

  async function loadChain(event: FormEvent, expiry?: string) {
    event.preventDefault();
    setChainLoading(true);
    setChainError("");
    if (!chainSymbol.trim()) {
      setChainError("Enter a symbol first.");
      setChainLoading(false);
      return;
    }
    try {
      const response = await advisorRequest("/api/advisor/analytics/chain", {
        method: "POST",
        body: JSON.stringify({
          symbol: chainSymbol.trim().toUpperCase(),
          target_dte: chainTargetDte ? Number(chainTargetDte) : 30,
          expiry: expiry || null,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Chain explorer returned ${response.status}`);
      }
      setChainResult(await response.json());
    } catch (requestError) {
      setChainError(requestError instanceof Error ? requestError.message : "Unable to load the option chain");
    } finally {
      setChainLoading(false);
    }
  }

  async function loadAlerts() {
    try {
      const [rulesResponse, templatesResponse, historyResponse] = await Promise.all([
        advisorRequest("/api/advisor/alerts"),
        advisorRequest("/api/advisor/alerts/gallery"),
        advisorRequest("/api/advisor/alerts/history?limit=30"),
      ]);
      if (rulesResponse.ok) {
        const data = await rulesResponse.json() as { rules: AlertRule[] };
        setAlertRules(data.rules || []);
      }
      if (templatesResponse.ok) {
        const data = await templatesResponse.json() as { templates: AlertTemplate[] };
        setAlertTemplates(data.templates || []);
      }
      if (historyResponse.ok) {
        const data = await historyResponse.json() as { events: AlertHistoryEvent[] };
        setAlertHistory(data.events || []);
      }
    } catch { /* backend may be down */ }
  }

  async function createGalleryAlert(template: AlertTemplate) {
    if (!alertGallerySymbol.trim()) {
      setAlertCenterError("Enter a symbol to attach the alert to.");
      return;
    }
    setAlertCenterError("");
    const threshold = alertGalleryThreshold.trim();
    try {
      const response = await advisorRequest("/api/advisor/alerts/gallery", {
        method: "POST",
        body: JSON.stringify({
          template_id: template.template_id,
          symbol: alertGallerySymbol.trim().toUpperCase(),
          threshold: threshold ? Number(threshold) : null,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Alert creation returned ${response.status}`);
      }
      await loadAlerts();
    } catch (requestError) {
      setAlertCenterError(requestError instanceof Error ? requestError.message : "Unable to create the alert");
    }
  }

  async function deleteAlert(ruleId: string) {
    try {
      const response = await advisorRequest(`/api/advisor/alerts/${encodeURIComponent(ruleId)}`, { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Alert deletion returned ${response.status}`);
      }
      setAlertRules((prev) => prev.filter((rule) => rule.rule_id !== ruleId));
    } catch (requestError) {
      setAlertCenterError(requestError instanceof Error ? requestError.message : "Unable to delete the alert");
    }
  }

  async function refreshScanSheet() {
    try {
      const statusResponse = await advisorRequest("/api/advisor/scanner/status");
      if (statusResponse.ok) setScannerStatus(await statusResponse.json() as ScannerStatus);
    } catch { /* backend may be down */ }
  }

  async function loadWatchlist() {
    try {
      const res = await advisorRequest("/api/advisor/watchlist");
      if (res.ok) {
        const data = await res.json() as { items: WatchlistItem[] };
        setWatchlist(data.items || []);
      }
    } catch { /* backend may be down */ }
  }

  async function addWatchSymbol(event: FormEvent) {
    event.preventDefault();
    const symbol = newWatchSymbol.trim().toUpperCase();
    if (!symbol) return;
    setWatchlistMsg("");
    setWatchlistError("");
    try {
      const res = await advisorRequest("/api/advisor/watchlist/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Add returned ${res.status}`);
      }
      setNewWatchSymbol("");
      setWatchlistMsg(`${symbol} added to the watchlist.`);
      await loadWatchlist();
    } catch (addError) {
      setWatchlistError(addError instanceof Error ? addError.message : "Could not add the symbol.");
    }
  }

  async function removeWatchSymbol(symbol: string) {
    setWatchlistError("");
    try {
      const res = await advisorRequest(`/api/advisor/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Remove returned ${res.status}`);
      }
      setWatchlist((prev) => prev.filter((item) => item.symbol !== symbol));
    } catch (removeError) {
      setWatchlistError(removeError instanceof Error ? removeError.message : "Could not remove the symbol.");
    }
  }

  async function analyzeWatchlist() {
    setWatchlistAnalyzing(true);
    setWatchlistError("");
    setWatchlistRankings([]);
    try {
      const res = await advisorRequest("/api/advisor/brain/analyze-watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Analysis returned ${res.status}`);
      }
      const data = await res.json() as { total_analyzed: number; rankings: Analysis[] };
      setWatchlistRankings(data.rankings || []);
    } catch (analyzeError) {
      setWatchlistError(analyzeError instanceof Error ? analyzeError.message : "Watchlist analysis failed.");
    } finally {
      setWatchlistAnalyzing(false);
    }
  }

  async function loadCommandCenter(event: FormEvent) {
    event.preventDefault();
    setCommandLoading(true);
    setCommandError("");
    setCommandCenterResult(null);
    try {
      let positions: unknown[] = [];
      if (commandPositions.trim()) {
        const parsed = JSON.parse(commandPositions.trim());
        positions = Array.isArray(parsed) ? parsed : [];
      }
      const res = await advisorRequest("/api/advisor/dashboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          capital: Number(commandCapital) || 100000,
          buying_power: Number(commandBuyingPower) || 50000,
          current_positions: positions,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Command center returned ${res.status}`);
      }
      setCommandCenterResult(await res.json() as DashboardResult);
    } catch (loadError) {
      setCommandError(loadError instanceof Error ? loadError.message : "Command center could not be loaded.");
    } finally {
      setCommandLoading(false);
    }
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

  const scanEntries = Object.entries(scannerStatus?.last_results?.symbols || {})
    .map(([symbol, entry]) => ({ symbol, ...entry }))
    .sort((a, b) => b.score - a.score);

  return (
    <main>
      <nav>
        <div className="brand"><span>θ</span> ThetaForge <small>PERSONAL TERMINAL · {VERSION}</small></div>
        <div className="nav-right">
          <a className="terminal-link" href="https://journal.astraiva.app/" target="_blank" rel="noreferrer">Public journal ↗</a>
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

      <section className="opportunity-panel command-center">
        <div className="opportunity-heading">
          <div><p className="eyebrow">COMMAND CENTER · ONE-CALL MARKET + PORTFOLIO READ</p><h2>At a glance</h2><p>One call to the Advisor: VIX and the current market regime, your account posture, portfolio-level delta/vega risk, and the best watchlist candidates over one week and one month. Read-only context — nothing here places an order.</p></div>
          <form className="scan-form" onSubmit={loadCommandCenter}>
            <input className="api" value={commandCapital} onChange={(event) => setCommandCapital(event.target.value)} placeholder="Equity (default 100,000)" aria-label="Portfolio equity" inputMode="decimal" />
            <input className="api" value={commandBuyingPower} onChange={(event) => setCommandBuyingPower(event.target.value)} placeholder="Buying power (default 50,000)" aria-label="Buying power" inputMode="decimal" />
            <textarea className="api" value={commandPositions} onChange={(event) => setCommandPositions(event.target.value)} rows={2} placeholder='[{"symbol":"AAPL","delta":-0.12,"vega":0.02,"margin":1500}]' aria-label="Open positions with delta/vega/margin" />
            <button disabled={commandLoading}>{commandLoading ? "Reading the tape…" : "Load command center"}</button>
          </form>
        </div>
        {commandError && <p className="error">{commandError}</p>}
        {commandCenterResult && <>
          <div className="command-grid">
            <div className="market-chip"><small>VIX</small><b>{commandCenterResult.vix.toFixed(1)}</b><span>{signalLabel(commandCenterResult.regime)} regime</span></div>
            <div className="command-card">
              <small>ACCOUNT</small>
              <span><b>${commandCenterResult.account.equity.toLocaleString()}</b> equity</span>
              <span><b>${commandCenterResult.account.buying_power.toLocaleString()}</b> buying power</span>
              <span><b>${commandCenterResult.account.capital_deployed.toLocaleString()}</b> deployed · <b>{commandCenterResult.account.capital_deployed_pct.toFixed(1)}%</b></span>
              <span><b>{commandCenterResult.account.num_positions}</b> open position{commandCenterResult.account.num_positions !== 1 ? "s" : ""}</span>
            </div>
            <div className={`command-card command-risk ${commandCenterResult.portfolio_risk.within_limits ? "" : "warn"}`}>
              <small>PORTFOLIO RISK</small>
              <span><small>NET DELTA</small><b>{commandCenterResult.portfolio_risk.net_delta >= 0 ? "+" : ""}{commandCenterResult.portfolio_risk.net_delta.toFixed(4)}</b></span>
              <span><small>NET VEGA</small><b>{commandCenterResult.portfolio_risk.net_vega >= 0 ? "+" : ""}{commandCenterResult.portfolio_risk.net_vega.toFixed(4)}</b></span>
              <span className={commandCenterResult.portfolio_risk.within_limits ? "ok" : "warn"}><small>LIMITS</small><b>{commandCenterResult.portfolio_risk.within_limits ? "Within limits" : "Outside limits"}</b></span>
            </div>
          </div>
          {(commandCenterResult.top_picks_1w.length > 0 || commandCenterResult.top_picks_1m.length > 0) && <div className="command-picks">
            {commandCenterResult.top_picks_1w.length > 0 && <div className="command-pick-list">
              <small>TOP PICKS — 1 WEEK</small>
              {commandCenterResult.top_picks_1w.map((pick) => <div className="command-pick" key={pick.symbol}>
                <b>{pick.symbol}</b><span className={`scan-score ${pick.score >= 0 ? "bull" : "bear"}`}>{pick.score >= 0 ? "+" : ""}{pick.score.toFixed(0)}</span><span>{signalLabel(pick.signal)}</span>
              </div>)}
            </div>}
            {commandCenterResult.top_picks_1m.length > 0 && <div className="command-pick-list">
              <small>TOP PICKS — 1 MONTH</small>
              {commandCenterResult.top_picks_1m.map((pick) => <div className="command-pick" key={pick.symbol}>
                <b>{pick.symbol}</b><span className={`scan-score ${pick.score >= 0 ? "bull" : "bear"}`}>{pick.score >= 0 ? "+" : ""}{pick.score.toFixed(0)}</span><span>{signalLabel(pick.signal)}</span>
              </div>)}
            </div>}
          </div>}
        </>}
        {commandCenterResult && <p className="scan-note">Regime from VIX tier ({commandCenterResult.vix.toFixed(1)}). Portfolio risk = sum of your position deltas and vegas — outside limits means concentrated directional or volatility exposure. Pick thresholds: suitability ≥ 70%.</p>}
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
          <div><p className="eyebrow">MARKET MAP · STOCKS · BONDS · SECTORS</p><h2>General-market context</h2><p>Cross-asset tape for stock and ETF positions: indices, bond yields, commodities, sector rotation, the yield curve, and a coarse risk-on / risk-off tilt. Read-only market context — it never sends orders.</p></div>
          <form className="scan-form" onSubmit={loadMarkets}><input className="api" value={marketsSymbols} onChange={(event) => setMarketsSymbols(event.target.value)} placeholder="Optional extra symbols, e.g. TSLA, JPM" aria-label="Extra symbols" /><button disabled={marketsLoading}>{marketsLoading ? "Reading the tape…" : "Load market map"}</button></form>
        </div>
        {marketsError && <p className="error">{marketsError}</p>}
        {markets && <>
          {markets.overview.risk_tilt.tilt === "risk_on" ? <div className="market-chip"><small>RISK TILT</small><b>Risk-on</b><span>{markets.overview.risk_tilt.indices_up} of {Object.keys(markets.overview.indices).length} indices up · credit confirming</span></div> : markets.overview.risk_tilt.tilt === "risk_off" ? <div className="market-chip"><small>RISK TILT</small><b>Risk-off</b><span>{markets.overview.risk_tilt.indices_down} of {Object.keys(markets.overview.indices).length} indices down</span></div> : <div className="market-chip"><small>RISK TILT</small><b>Mixed</b><span>Equity and credit not confirming each other</span></div>}
          <p className="scan-note">Yield curve: {markets.overview.yield_curve.shape === "inverted" ? "inverted — premium selling discouraged" : markets.overview.yield_curve.shape === "normal" ? "normal" : "flat"} · 13-wk {markets.overview.yield_curve.short != null ? markets.overview.yield_curve.short.toFixed(2) : "—"}% · 10-yr {markets.overview.yield_curve.long != null ? markets.overview.yield_curve.long.toFixed(2) : "—"}% · 30-yr {markets.overview.yield_curve.very_long != null ? markets.overview.yield_curve.very_long.toFixed(2) : "—"}%</p>
          <div className="stock-list">
            {Object.entries(markets.overview.indices).map(([symbol, read]) => <div className="stock-card" key={symbol}><small>INDEX</small><b>{read.label}</b><span>{read.level?.toFixed(2)} · {read.change_1d_pct != null ? `${read.change_1d_pct >= 0 ? "+" : ""}${read.change_1d_pct.toFixed(2)}%` : "—"}</span><small className={read.trend === "uptrend" ? "positive" : read.trend === "downtrend" ? "negative" : ""}>{read.trend}</small></div>)}
            {Object.entries(markets.overview.bonds).map(([symbol, read]) => <div className="stock-card" key={symbol}><small>RATE / BOND</small><b>{read.label}</b><span>{read.level_pct != null ? `${read.level_pct.toFixed(2)}%` : read.level != null ? read.level.toFixed(2) : "—"}{read.change_bp != null ? ` · ${read.change_bp >= 0 ? "+" : ""}${read.change_bp.toFixed(1)} bp` : read.change_1d_pct != null ? ` · ${read.change_1d_pct >= 0 ? "+" : ""}${read.change_1d_pct.toFixed(2)}%` : ""}</span><small className={read.trend === "uptrend" ? "positive" : read.trend === "downtrend" ? "negative" : ""}>{read.trend}</small></div>)}
            {Object.entries(markets.overview.commodities).map(([symbol, read]) => <div className="stock-card" key={symbol}><small>COMMODITY</small><b>{read.label}</b><span>{read.level?.toFixed(2)} · {read.change_1d_pct != null ? `${read.change_1d_pct >= 0 ? "+" : ""}${read.change_1d_pct.toFixed(2)}%` : "—"}</span><small className={read.trend === "uptrend" ? "positive" : read.trend === "downtrend" ? "negative" : ""}>{read.trend}</small></div>)}
            {Object.entries(markets.overview.sectors).map(([sector, value]) => <div className="stock-card" key={sector}><small>SECTOR</small><b>{sector}</b><span>{value >= 0 ? "+" : ""}{value.toFixed(1)}%</span><small className={value >= 0 ? "positive" : "negative"}>{value >= 0 ? "leading" : "lagging"}</small></div>)}
          </div>
          {Object.keys(markets.symbols).length ? <div className="trade-list">{Object.entries(markets.symbols).map(([symbol, read]) => <article className="trade-card" key={symbol}>
            <div className="trade-title"><p className="eyebrow">{symbol} · ${read.price.toFixed(2)} · {read.volatility_20d_annualized != null ? `${(read.volatility_20d_annualized * 100).toFixed(1)}% 20d vol` : "vol n/a"}</p><h3>{signalLabel(read.read)}</h3><p>RSI {read.rsi_14.toFixed(0)} · ADX {read.adx.toFixed(0)} · {read.macd_bullish ? "MACD bullish" : "MACD bearish"} · {read.above_200d ? "above" : "below"} 200-day</p></div>
            <div className="trade-metrics"><span><small>1D</small><b>{read.change_1d_pct >= 0 ? "+" : ""}{read.change_1d_pct.toFixed(2)}%</b></span><span><small>1M</small><b>{read.change_1m_pct >= 0 ? "+" : ""}{read.change_1m_pct.toFixed(2)}%</b></span><span><small>VS 52W HIGH</small><b>{read.percent_off_52w_high.toFixed(1)}%</b></span><span><small>VOLUME</small><b>{read.volume_ratio != null ? `${read.volume_ratio.toFixed(2)}x` : "—"}</b></span></div>
          </article>)}</div> : null}
        </>}
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">WATCHLIST · YOUR SYMBOL UNIVERSE</p><h2>Watchlist</h2><p>The symbols you follow, saved on the Advisor. The watchlist feeds the Brain's watchlist analysis with your default delta/DTE preferences for idea generation. Managing the list here never places an order.</p></div>
          <form className="scan-form" onSubmit={addWatchSymbol}><input className="api" value={newWatchSymbol} onChange={(event) => setNewWatchSymbol(event.target.value)} placeholder="Add symbol, e.g. NVDA" aria-label="Watchlist symbol" maxLength={8} /><button type="submit">+ Add</button></form>
        </div>
        {watchlistError && <p className="error">{watchlistError}</p>}
        {watchlistMsg && <p className="scan-note">{watchlistMsg}</p>}
        <div className="watchlist-bar">
          <p className="scan-note">{watchlist.length} symbols on the watchlist · Analyze runs the full Brain pass (option chain, flow, and vol context) over every member and ranks them by score.</p>
          <button type="button" onClick={analyzeWatchlist} disabled={watchlistAnalyzing || watchlist.length === 0}>{watchlistAnalyzing ? "Analyzing watchlist…" : "Analyze watchlist"}</button>
        </div>
        {watchlist.length === 0 ? <p className="notif-empty">The watchlist is empty. Add symbols above — they become your focused universe for watchlist analysis.</p> : <div className="watchlist-table">
          <div className="watchlist-head"><span>SYMBOL</span><span>DELTA / DTE</span><span>TAGS</span><span>ADDED</span><span /></div>
          {watchlist.map((item) => <div className="watchlist-row" key={item.symbol}>
            <b>{item.symbol}</b>
            <span>{item.custom_delta} Δ / {item.custom_dte} DTE</span>
            <span>{item.tags.length ? item.tags.join(", ") : (item.notes || "—")}</span>
            <span>{new Date(item.added_at).toLocaleDateString()}</span>
            <button type="button" aria-label={`Remove ${item.symbol} from watchlist`} onClick={() => removeWatchSymbol(item.symbol)}>×</button>
          </div>)}
        </div>}
        {watchlistRankings.length > 0 && <>
          <h3 className="watchlist-rank-title">Watchlist ranking — best Brain scores</h3>
          <div className="watchlist-rankings">
            {watchlistRankings.map((ranking) => <div className="watchlist-ranking" key={ranking.symbol}>
              <b>{ranking.symbol}</b>
              <span className={`scan-score ${ranking.overall_score >= 0 ? "bull" : "bear"}`}>{ranking.overall_score >= 0 ? "+" : ""}{ranking.overall_score.toFixed(0)}</span>
              <span>{signalLabel(ranking.overall_signal)} · {signalLabel(ranking.regime)} · {signalLabel(ranking.best_strategy)}</span>
              <small>${ranking.stock_price.toFixed(2)} · {ranking.confidence}% confidence</small>
            </div>)}
          </div>
        </>}
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">POSITION MANAGEMENT · THETA EXITS</p><h2>Manage open short-premium positions</h2><p>The research playbook, applied to positions you already hold: take profit at 50% of max credit, close or roll inside the 21-DTE gamma window, stop at 2× the credit, and never hold short premium through an earnings print. Read-only guidance — closing orders still go through the paper Bridge.</p></div>
          <form className="scan-form" onSubmit={loadManagement}><textarea className="api" value={managementPositions} onChange={(event) => setManagementPositions(event.target.value)} rows={3} placeholder='[{"symbol":"AAPL","strategy":"bull_put","short_strike":200,"long_strike":190,"credit_received":1.20,"dte":30}]' aria-label="Open positions JSON" /><input className="api" value={managementCapital} onChange={(event) => setManagementCapital(event.target.value)} placeholder="Portfolio capital (default 100,000)" aria-label="Portfolio capital" /><input className="api" value={managementRealized} onChange={(event) => setManagementRealized(event.target.value)} placeholder="Realized P&L, negative = losses" aria-label="Realized P&L" /><button disabled={managementLoading}>{managementLoading ? "Evaluating…" : "Run management check"}</button></form>
        </div>
        {managementError && <p className="error">{managementError}</p>}
        {managementResult && <>
          {managementResult.portfolio.can_open_new ? <div className="market-chip"><small>PORTFOLIO</small><b>Green to open</b><span>{managementResult.portfolio.num_positions} of {managementResult.portfolio.max_positions} positions · max {dollars(managementResult.portfolio.max_symbol_slice)} per symbol · drawdown {managementResult.portfolio.realized_drawdown_pct.toFixed(1)}%</span></div> : <div className="market-chip"><small>PORTFOLIO</small><b>Blocked</b><span>{managementResult.portfolio.violations.join(" · ") || "Portfolio limits exceeded"}</span></div>}
          <div className="trade-list">{managementResult.actions.map((action, index) => <article className="trade-card" key={`${action.symbol}-${index}`}>
            <div className="trade-title"><p className="eyebrow">{action.symbol} · {signalLabel(action.strategy)} · {action.dte != null ? `${action.dte} DTE` : "DTE n/a"}</p><h3>{signalLabel(action.action)}</h3><p>{action.reason}</p></div>
            <div className="trade-metrics"><span><small>PROFIT CAPTURED</small><b>{action.profit_pct != null ? percent(action.profit_pct) : "—"}</b></span><span className={action.loss_to_credit != null && action.loss_to_credit >= 2 ? "loss" : ""}><small>LOSS-TO-CREDIT</small><b>{action.loss_to_credit != null ? `${action.loss_to_credit.toFixed(1)}x` : "—"}</b></span><span><small>SHORT STRIKE</small><b>{action.short_strike.toFixed(2)}{action.spot != null ? ` · spot ${action.spot.toFixed(2)}` : ""}</b></span><span className="profit"><small>MAX PROFIT</small><b>{dollars(action.max_profit)}</b></span><span className="loss"><small>MAX LOSS</small><b>{action.max_loss != null ? dollars(action.max_loss) : "—"}</b></span></div>
          </article>)}</div>
        </>}
      </section>

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

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">BRAIN SCANNER · LAST FULL RUN</p><h2>Scan sheet</h2><p>The per-symbol output of the last complete background scan: every analyzed underlying with its composite score, signal, regime, strategy fit, and the vol/flow context the Brain saw. A score here is a discovery — a real trade still has to pass the full chain, portfolio, and paper-Bridge checks.</p>
          {scannerStatus && <div className="market-chip"><small>SCANNER</small><b>{scannerStatus.market_open ? "Market open · scanning" : "Markets closed"}</b><span>{scannerStatus.is_running ? "running" : "idle"} · last run {scannerStatus.last_run ? new Date(scannerStatus.last_run).toLocaleString() : "—"}{scannerStatus.next_run ? ` · next ${new Date(scannerStatus.next_run).toLocaleTimeString()}` : ""} · every {scannerStatus.interval_seconds ? `${scannerStatus.interval_seconds}s` : "300s"}</span></div>}
          </div>
          <button type="button" onClick={refreshScanSheet} disabled={!scannerStatus}>Refresh</button>
        </div>
        {scanEntries.length === 0 ? <p className="notif-empty">No scan results yet. The background scanner records each symbol it analyzed on its last full run; results appear here after the first scan.</p> : <div className="scan-sheet">
          <div className="scan-sheet-head"><span>SYMBOL</span><span>SCORE</span><span>SIGNAL</span><span>STRATEGY</span><span>REGIME</span><span>IVR</span><span>TERM</span><span>FLOW</span><span>EXP MOVE</span><span>GEX</span><span>VRP</span></div>
          {scanEntries.map((entry) => <div className="scan-sheet-row" key={entry.symbol}>
            <b>{entry.symbol}</b>
            <span className={`scan-score ${entry.score >= 0 ? "bull" : "bear"}`}>{entry.score >= 0 ? "+" : ""}{entry.score.toFixed(0)}</span>
            <span>{signalLabel(entry.signal)}</span>
            <span>{signalLabel(entry.strategy)}</span>
            <span>{entry.regime ? signalLabel(entry.regime) : "—"}</span>
            <span>{typeof entry.iv_rank === "number" ? entry.iv_rank.toFixed(0) : "—"}</span>
            <span>{entry.term_structure ? signalLabel(entry.term_structure) : "—"}</span>
            <span>{entry.flow_bias ? signalLabel(entry.flow_bias) : "—"}</span>
            <span>{typeof entry.expected_move_pct === "number" ? `±${entry.expected_move_pct.toFixed(1)}%` : "—"}</span>
            <span>{entry.gex_regime ? signalLabel(entry.gex_regime) : "—"}</span>
            <span>{typeof entry.vol_risk_premium === "number" ? `${entry.vol_risk_premium.toFixed(1)}%` : "—"}</span>
          </div>)}
        </div>}
        <p className="scan-note">Score sign = expected direction of the setup · IVR = IV rank vs the symbol's own history · term = VIX term-structure read · flow = put/call flow bias · exp move = ATM expected move · GEX = dealer gamma regime · VRP = vol risk premium.</p>
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">EQUITY ENGINE · MOMENTUM LONGS</p><h2>Stock and ETF signals</h2><p>The second brain scans liquid stocks and the ETF core for trend-plus-momentum setups: 200d/50d trend alignment, 6-month absolute momentum, ADX ≥ 20, RSI ≤ 80, and at least market relative strength. Signals above the 70 threshold are surfaced here and scored into a full recommendation (1% risk, 2× ATR stop, 2R target).</p>{equityScannerStatus && <div className="market-chip"><small>EQUITY SCANNER</small><b>{equityScannerStatus.market_open ? "Market open · scanning" : "Markets closed"}</b><span>{equityScannerStatus.symbols_scanned_last_run} symbols last run · {equityScannerStatus.passes} qualifying · {equityScannerStatus.pending_count} pending{equityScannerStatus.last_error ? ` · ${equityScannerStatus.last_error}` : ""}</span></div>}</div>
        </div>
        {equityRecError && <p className="error">{equityRecError}</p>}
        {equityNotifications.length === 0 ? <p className="notif-empty">No unacknowledged equity signals right now. The scanner writes one notification per qualifying symbol per market day; acknowledging a signal removes it from this queue.</p> : <div className="stock-list">{equityNotifications.map((notification) => {
          const reco = equityRecs[notification.symbol];
          return <div className="stock-card" key={notification.id}>
          <small>{signalLabel(notification.read)} · {signalLabel(notification.trend)} · score {notification.score.toFixed(0)}</small>
          <b>{notification.symbol}</b>
          <span>{signalLabel(notification.signal)}</span>
          {reco ? <div className="equity-reco"><div className="trade-metrics"><span><small>SHARES</small><b>{reco.shares}</b></span><span><small>ENTRY</small><b>${reco.entry_price.toFixed(2)}</b></span><span className="loss"><small>STOP</small><b>${reco.stop_price.toFixed(2)}</b></span><span className="profit"><small>TARGET</small><b>{reco.target_price ? `$${reco.target_price.toFixed(2)}` : "—"}</b></span><span className="loss"><small>RISK</small><b>{dollars(reco.max_loss_total)}</b></span></div><p>{reco.gate ? <b className="loss">{reco.gate_reason || "Blocked by an entry gate."}</b> : reco.rationale}</p></div> : <div className="paper-trade-action"><button type="button" onClick={() => loadEquityRecommendation(notification)} disabled={equityRecLoading === notification.symbol}>{equityRecLoading === notification.symbol ? "Scoring setup…" : "Score recommendation"}</button></div>}
          <button className="notif-ack" aria-label={`Acknowledge ${notification.symbol} equity signal`} onClick={() => acknowledgeEquity(notification.id)}>×</button>
        </div>;
        })}</div>}
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">EQUITY POSITION MANAGEMENT · EXITS</p><h2>Manage open stock and ETF longs</h2><p>Rule-driven exits for the equity engine: 2× ATR hard stop, chandelier trail once the position is +1R, 2R take-profit, 60-day time exit, and pre-earnings / pre-macro blackout exits. Read-only guidance — closing orders still go through the paper Bridge.</p></div>
          <form className="scan-form" onSubmit={loadEquityManagement}><textarea className="api" value={equityPositions} onChange={(event) => setEquityPositions(event.target.value)} rows={3} placeholder='[{"symbol":"AAPL","entry_price":210.00,"stop_price":196.00,"target_price":225.00,"highest_high":212.50,"shares":10}]' aria-label="Open equity positions JSON" /><input className="api" value={equityManagementCapital} onChange={(event) => setEquityManagementCapital(event.target.value)} placeholder="Portfolio capital (default 5,000)" aria-label="Equity portfolio capital" /><button disabled={equityManagementLoading}>{equityManagementLoading ? "Evaluating…" : "Run equity management check"}</button></form>
        </div>
        {equityManagementError && <p className="error">{equityManagementError}</p>}
        {equityManagementResult && <div className="trade-list">{equityManagementResult.actions.map((action, index) => <article className="trade-card" key={`${action.symbol}-${index}`}>
          <div className="trade-title"><p className="eyebrow">{action.symbol} · {action.shares} shares{action.current_price != null ? ` · spot ${action.current_price.toFixed(2)}` : ""}</p><h3>{signalLabel(action.action)}</h3><p>{action.reason}</p></div>
          <div className="trade-metrics"><span><small>HIGHEST HIGH</small><b>{action.highest_high != null ? `$${action.highest_high.toFixed(2)}` : "—"}</b></span><span><small>ACTION</small><b>{signalLabel(action.action)}</b></span></div>
        </article>)}</div>}
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">STRATEGY P/L CALCULATOR · AT EXPIRY</p><h2>Model any structure's payoff</h2><p>Enter the legs and spot to get the classic at-expiry P/L curve: max profit, max loss, breakevens, risk/reward, and probability of profit at expiry when you supply IV and DTE. Pure math over your entries — the premium comes from your own numbers, never invented here.</p></div>
        </div>
        <form className="scan-form" onSubmit={calculatePnl}>
          <div className="pnl-legs">
            {pnlLegs.map((leg, index) => (
              <div className="pnl-leg" key={index}>
                <select aria-label={`Leg ${index + 1} action`} value={leg.action} onChange={(event) => updatePnlLeg(index, "action", event.target.value as "BUY" | "SELL")}><option value="SELL">SELL</option><option value="BUY">BUY</option></select>
                <select aria-label={`Leg ${index + 1} type`} value={leg.option_type} onChange={(event) => updatePnlLeg(index, "option_type", event.target.value as "call" | "put")}><option value="put">PUT</option><option value="call">CALL</option></select>
                <input aria-label={`Leg ${index + 1} strike`} value={leg.strike} onChange={(event) => updatePnlLeg(index, "strike", event.target.value)} placeholder="Strike" inputMode="decimal" />
                <input aria-label={`Leg ${index + 1} premium`} value={leg.entry_price} onChange={(event) => updatePnlLeg(index, "entry_price", event.target.value)} placeholder="Premium / share" inputMode="decimal" />
                {pnlLegs.length > 1 && <button type="button" className="pnl-remove" aria-label={`Remove leg ${index + 1}`} onClick={() => removePnlLeg(index)}>×</button>}
              </div>
            ))}
          </div>
          <div className="pnl-inputs">
            <label>Spot<input className="api" value={pnlSpot} onChange={(event) => setPnlSpot(event.target.value)} inputMode="decimal" aria-label="Spot price" /></label>
            <label>Contracts<input className="api" value={pnlContracts} onChange={(event) => setPnlContracts(event.target.value)} inputMode="numeric" aria-label="Contracts" /></label>
            <label>IV<input className="api" value={pnlIv} onChange={(event) => setPnlIv(event.target.value)} inputMode="decimal" aria-label="Implied volatility" /></label>
            <label>DTE<input className="api" value={pnlDte} onChange={(event) => setPnlDte(event.target.value)} inputMode="numeric" aria-label="Days to expiry" /></label>
            <button disabled={pnlLoading}>{pnlLoading ? "Calculating…" : "Run P/L calculator"}</button>
          </div>
          <div className="pnl-leg-actions"><button type="button" onClick={addPnlLeg}>+ Add leg</button></div>
        </form>
        {pnlError && <p className="error">{pnlError}</p>}
        {pnlResult && !pnlResult.error && <div className="pnl-result">
          <div className="pnl-grid">
            <span className="profit"><small>MAX PROFIT</small><b>{dollars(pnlResult.max_profit)}</b></span>
            <span className="loss"><small>MAX LOSS</small><b>{dollars(Math.abs(pnlResult.max_loss))}</b></span>
            <span><small>RISK / REWARD</small><b>1 : {pnlResult.risk_reward.toFixed(2)}</b></span>
            <span><small>NET ENTRY</small><b>{pnlResult.net_entry >= 0 ? "+" : ""}${pnlResult.net_entry.toFixed(2)}</b></span>
            <span><small>BREAKEVEN{(pnlResult.breakevens || []).length > 1 ? "S" : ""}</small><b>{(pnlResult.breakevens || []).map((be) => be.toFixed(2)).join(" · ") || "—"}</b></span>
            <span><small>POP AT EXPIRY</small><b>{pnlResult.pop_at_expiry != null ? `${pnlResult.pop_at_expiry.toFixed(1)}%` : "Add IV + DTE"}</b></span>
            <span><small>PER SHARE</small><b>${pnlResult.net_entry_per_share.toFixed(2)}</b></span>
            <span><small>SPOT</small><b>${pnlResult.spot.toFixed(2)}</b></span>
          </div>
          <PnLCurve result={pnlResult} />
        </div>}
        {pnlResult?.error && <p className="error">{pnlResult.error}</p>}
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">DEALER GAMMA EXPOSURE · HEATMAP</p><h2>Where dealer hedging pins or amplifies</h2><p>Net gamma per strike from the free option chain: positive GEX acts as a price magnet (dealers sell rallies, buy dips), negative GEX amplifies moves, and the zero-gamma level is where realized volatility peaks. Free-chain GEX is an approximation of institutional positioning — a context gauge, not a signal by itself.</p></div>
          <form className="scan-form" onSubmit={loadGex}><input className="api" value={gexSymbol} onChange={(event) => setGexSymbol(event.target.value)} placeholder="Symbol, e.g. SPY" aria-label="GEX symbol" maxLength={8} /><button disabled={gexLoading}>{gexLoading ? "Reading chain…" : "Load GEX heatmap"}</button></form>
        </div>
        {gexError && <p className="error">{gexError}</p>}
        {gexResult && !gexResult.error && <div className="gex-result">
          <div className="gex-summary">
            <span><small>UNDERLYING</small><b>${gexResult.underlying.toFixed(2)}</b></span>
            <span><small>NET GEX</small><b>{gexResult.net_gex >= 0 ? "+" : ""}{gexResult.net_gex.toFixed(1)}M</b></span>
            <span><small>CALL GEX</small><b>{gexResult.total_call_gex.toFixed(1)}M</b></span>
            <span><small>PUT GEX</small><b>{gexResult.total_put_gex.toFixed(1)}M</b></span>
            <span><small>ZERO GAMMA</small><b>{gexResult.zero_gamma_strike != null ? `$${gexResult.zero_gamma_strike.toFixed(0)}` : "—"}</b></span>
            <span><small>REGIME</small><b>{signalLabel(gexResult.gex_regime)}</b></span>
          </div>
          <div className="gex-strip">{Object.entries(gexResult.strike_gex || {}).sort(([a], [b]) => Number(a) - Number(b)).map(([strike, net]) => {
            const value = Number(net);
            const isWall = Math.abs(value) >= Math.max(5, Math.abs(gexResult.net_gex) * 0.9);
            return <div key={strike} className={`gex-cell ${gexHeatBucket(value)}${isWall && gexHeatBucket(value) !== "flat" ? " wall" : ""}`} title={`${strike}: ${value >= 0 ? "+" : ""}${value.toFixed(2)}M dealer GEX`}><small>${Number(strike).toFixed(0)}{gexResult.zero_gamma_strike != null && Math.abs(Number(strike) - gexResult.zero_gamma_strike) < 0.5 ? " · ZG" : ""}</small><b>{value >= 0 ? "+" : ""}{value.toFixed(1)}</b></div>;
          })}</div>
          <p className="scan-note">Cell color = |dealer GEX| at that strike. Red = positive wall (support), amber = hot, lime = elevated, grey = flat.</p>
        </div>}
        {gexResult?.error && <p className="error">{gexResult.error}</p>}
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">OPTIONS CHAIN EXPLORER · FULL TABLE</p><h2>Chain explorer</h2><p>The desk-style chain view: every strike with its call and put sides side-by-side (bid/ask/mid, IV, open interest, volume, greeks), plus the expiry's desk readings — ATM IV, the ATM straddle's expected move, max pain, put/call ratios, IV skew, and IV rank vs this symbol's own history.</p></div>
          <form className="scan-form" onSubmit={(event) => loadChain(event)}>
            <input className="api" value={chainSymbol} onChange={(event) => setChainSymbol(event.target.value)} placeholder="Symbol, e.g. SPY" aria-label="Chain symbol" maxLength={8} />
            <input className="api" value={chainTargetDte} onChange={(event) => setChainTargetDte(event.target.value)} placeholder="Target DTE (default 30)" aria-label="Target days to expiry" inputMode="numeric" />
            <button disabled={chainLoading}>{chainLoading ? "Reading chain…" : "Load option chain"}</button>
          </form>
        </div>
        {chainError && <p className="error">{chainError}</p>}
        {chainResult && !chainResult.error && <div className="chain-result">
          <div className="chain-utils">
            <div className="chain-expiries">{chainResult.expiries.map((entry) => <button type="button" key={entry.expiry} className={`chain-expiry ${chainResult.expiry === entry.expiry ? "selected" : ""}`} onClick={(event) => loadChain(event, entry.expiry)}>{entry.dte} DTE · {entry.expiry.slice(5)}</button>)}</div>
            <div className="chain-summary">
              <span><small>ATM IV</small><b>{chainResult.summary.atm_iv > 0 ? `${(chainResult.summary.atm_iv * 100).toFixed(1)}%` : "—"}</b></span>
              {typeof chainResult.summary.iv_rank === "number" && <span><small>IV RANK</small><b>{chainResult.summary.iv_rank.toFixed(0)}</b></span>}
              {typeof chainResult.summary.iv_percentile === "number" && <span><small>IV PCT</small><b>{chainResult.summary.iv_percentile.toFixed(0)}</b></span>}
              {typeof chainResult.summary.nvrp === "number" && <span><small>NVRP {chainResult.summary.hv_20 != null ? `(HV ${(chainResult.summary.hv_20 * 100).toFixed(1)}%)` : ""}</small><b>{(chainResult.summary.nvrp * 100).toFixed(1)}%</b></span>}
              <span><small>EXP MOVE</small><b>±{(chainResult.summary.expected_move_pct).toFixed(1)}%</b></span>
              <span><small>EXP BAND</small><b>${chainResult.summary.expected_move_low.toFixed(2)}–${chainResult.summary.expected_move_high.toFixed(2)}</b></span>
              <span><small>MAX PAIN</small><b>{chainResult.summary.max_pain_strike != null ? `$${chainResult.summary.max_pain_strike.toFixed(0)}` : "—"}</b></span>
              <span><small>P/C OI</small><b>{chainResult.summary.put_call_oi_ratio != null ? chainResult.summary.put_call_oi_ratio.toFixed(2) : "—"}</b></span>
              <span><small>P/C VOL</small><b>{chainResult.summary.put_call_volume_ratio != null ? chainResult.summary.put_call_volume_ratio.toFixed(2) : "—"}</b></span>
              {chainResult.summary.iv_skew && <span><small>SKEW</small><b>{signalLabel(chainResult.summary.iv_skew.regime)}</b></span>}
              <span><small>STRADDLE</small><b>${chainResult.summary.atm_straddle_mid.toFixed(2)}</b></span>
            </div>
          </div>
          <div className="chain-table">
            <div className="chain-table-head"><span>STRIKE</span><span>CALL BID/ASK</span><span>CALL IV</span><span>CALL OI</span><span>CALL DELTA</span><span>P/C OI</span><span>PUT DELTA</span><span>PUT OI</span><span>PUT IV</span><span>PUT BID/ASK</span></div>
            {chainResult.table.map((row) => <div className={`chain-row ${row.strike === chainResult.underlying ? "atm" : ""}`} key={row.strike}>
              <b>${row.strike.toFixed(0)}</b>
              <span>{row.call ? `${row.call.bid.toFixed(2)} / ${row.call.ask.toFixed(2)}` : "—"}</span>
              <span>{row.call && row.call.iv > 0 ? `${(row.call.iv * 100).toFixed(1)}%` : "—"}</span>
              <span>{row.call ? row.call.open_interest.toLocaleString() : "—"}</span>
              <span>{row.call ? row.call.delta.toFixed(2) : "—"}</span>
              <span>{row.put_call_oi_ratio != null ? row.put_call_oi_ratio.toFixed(2) : "—"}</span>
              <span>{row.put ? row.put.delta.toFixed(2) : "—"}</span>
              <span>{row.put ? row.put.open_interest.toLocaleString() : "—"}</span>
              <span>{row.put && row.put.iv > 0 ? `${(row.put.iv * 100).toFixed(1)}%` : "—"}</span>
              <span>{row.put ? `${row.put.bid.toFixed(2)} / ${row.put.ask.toFixed(2)}` : "—"}</span>
            </div>)}
          </div>
          <p className="scan-note">Underlying ${chainResult.underlying.toFixed(2)} · {chainResult.summary.call_oi_total.toLocaleString()} call OI vs {chainResult.summary.put_oi_total.toLocaleString()} put OI on this expiry{chainResult.summary.iv_skew ? ` · ${chainResult.summary.iv_skew.reasoning}` : ""}.</p>
        </div>}
        {chainResult?.error && <p className="error">{chainResult.error}</p>}
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">STRATEGY PLAYBOOKS · EDUCATION</p><h2>Playbook library</h2><p>The strategies this engine actually evaluates, tied to the gates the Brain, Recommender, and Trade Manager already enforce — with each strategy's real risk profile spelled out. Education, never an order path.</p></div>
        </div>
        <div className="stock-list playbook-grid">
          {playbooks.map((playbook) => <button type="button" key={playbook.id} className={`stock-card ${playbookOpen === playbook.id ? "selected" : ""}`} onClick={() => openPlaybook(playbook.id)}><small>{signalLabel(playbook.strategy_type)} · {signalLabel(playbook.risk_profile)}</small><b>{playbook.name}</b><span>{playbook.premium_printer ? "premium printer" : "defined-risk"} · open to read</span></button>)}
        </div>
        {playbookLoading && <p className="scan-note">Loading playbook…</p>}
        {playbookError && <p className="error">{playbookError}</p>}
        {playbookOpen && playbookDetail[playbookOpen] && !playbookDetail[playbookOpen].error && (
          <div className="playbook-detail">
            <section><small>MECHANICS</small><p>{playbookDetail[playbookOpen].mechanics}</p></section>
            <section><small>ENTRY RULES</small><p>{playbookDetail[playbookOpen].entry_rules}</p></section>
            <section><small>MANAGEMENT</small><p>{playbookDetail[playbookOpen].management}</p></section>
            <section><small>COMMON MISTAKES</small><p>{playbookDetail[playbookOpen].common_mistakes}</p></section>
            <section><small>BEST FOR</small><p>{playbookDetail[playbookOpen].best_for}</p></section>
            <section><small>RISK WARNING</small><p>{playbookDetail[playbookOpen].risk_warning}</p></section>
          </div>
        )}
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">ALERT NOTIFICATIONS · WEBHOOK</p><h2>Route triggered alerts to chat</h2><p>When a saved alert rule fires during a scan, the Advisor posts the event to a Discord or Slack-compatible webhook URL. Delivery is fire-and-forget from a background thread — a down webhook never blocks the scan.</p></div>
        </div>
        <form className="scan-form" onSubmit={saveWebhook}>
          <input className="api" value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} placeholder="https://discord.com/api/webhooks/…" aria-label="Alert webhook URL" />
          <button disabled={webhookSaving}>{webhookSaving ? "Saving…" : "Save webhook"}</button>
          <button type="button" className="webhook-clear" onClick={clearWebhook} disabled={webhookSaving}>Disable</button>
        </form>
        {webhookStatus && <p className="scan-note">{webhookStatus}</p>}
      </section>

      <section className="opportunity-panel">
        <div className="opportunity-heading">
          <div><p className="eyebrow">ALERT RULES · THRESHOLDS · HISTORY</p><h2>Alert center</h2><p>Rule-based triggers evaluated on every scan: score, IV rank/percentile, price, VIX, put/call ratio, GEX regime, theoretical edge, and earnings proximity. Create one in a click from the gallery below, or manage the rules already saved on the Advisor. Fired rules land in the history list.</p></div>
        </div>
        {alertCenterError && <p className="error">{alertCenterError}</p>}
        <div className="alert-gallery">
          <div className="alert-gallery-head"><label>Watch symbol<input className="api" value={alertGallerySymbol} onChange={(event) => setAlertGallerySymbol(event.target.value)} placeholder="Symbol, e.g. SPY" aria-label="Alert watch symbol" maxLength={8} /></label><label>Threshold (optional)<input className="api" value={alertGalleryThreshold} onChange={(event) => setAlertGalleryThreshold(event.target.value)} placeholder="Leave blank for default" aria-label="Alert threshold override" inputMode="decimal" /></label></div>
          <div className="alert-templates">{alertTemplates.map((template) => <div className="alert-template" key={template.template_id}>
            <div><b>{template.name}</b><p>{template.description}</p><small>{signalLabel(template.alert_type)} · default {template.default_threshold} · {template.priority}</small></div>
            <button type="button" onClick={() => createGalleryAlert(template)}>+ Add</button>
          </div>)}</div>
        </div>
        <div className="alert-split">
          <div className="alert-column">
            <h3>Active rules ({alertRules.length})</h3>
            {alertRules.length === 0 ? <p className="notif-empty">No alert rules saved on the Advisor yet. Add one from the gallery above.</p> : alertRules.map((rule) => <div className="alert-rule" key={rule.rule_id}>
              <div><b>{rule.symbol}</b><span>{signalLabel(rule.alert_type)} {typeof rule.threshold === "number" ? `≥ ${rule.threshold}` : `= ${rule.threshold}`} · {rule.priority}</span></div>
              <button type="button" aria-label={`Delete ${rule.symbol} ${rule.alert_type} alert`} onClick={() => deleteAlert(rule.rule_id)}>×</button>
            </div>)}
          </div>
          <div className="alert-column">
            <h3>Triggered history ({alertHistory.length})</h3>
            {alertHistory.length === 0 ? <p className="notif-empty">No alerts have fired yet. Rules evaluate on the next scan once they have the market data they need.</p> : alertHistory.map((event, index) => <div className="alert-rule history" key={`${event.rule_id}-${index}`}>
              <div><b>{event.symbol}</b><span>{signalLabel(event.alert_type)} · {event.message}</span><small>{new Date(event.timestamp).toLocaleString()}</small></div>
            </div>)}
          </div>
        </div>
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
