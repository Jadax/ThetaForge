import Link from "next/link";
import tradesData from "../../trades.json";

type Leg = {
  action: string;
  type: string;
  strike: number | null;
  expiry: string | null;
  dte: number | null;
};

type Research = { label: string; url: string };

type Trade = {
  id: string;
  symbol: string;
  opened: string;
  closed: string | null;
  status: "open" | "closed";
  strategy: string;
  label: string;
  legs: Leg[];
  entry_ivr: number;
  dte_at_entry: number | null;
  capital_at_risk: number;
  max_profit: number;
  net_pnl: number;
  net_pnl_pct: number;
  reason: string;
  research: Research[];
  tags: string[];
  exit_note: string;
  timestamp: string;
};

type Trader = { name: string; handle: string; tagline: string };

type TradesFile = {
  trader: Trader;
  as_of: string;
  account_equity: number;
  paper_only: boolean;
  trades: Trade[];
};

const trades = (tradesData as TradesFile).trades;
const trader = (tradesData as TradesFile).trader;
const asOf = (tradesData as TradesFile).as_of;

const usd = (value: number) =>
  `${value >= 0 ? "+" : "-"}$${Math.abs(value).toFixed(0)}`;
const pct = (value: number) =>
  `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
const strategyLabel = (value: string) => value.replaceAll("_", " ");
const dateLabel = (value: string) => {
  const [year, month, day] = value.split("-");
  return `${month}/${day}/${year.slice(2)}`;
};

const closed = trades.filter((trade) => trade.status === "closed");
const winners = closed.filter((trade) => trade.net_pnl > 0);
const losers = closed.filter((trade) => trade.net_pnl <= 0);
const grossWin = winners.reduce((sum, trade) => sum + trade.net_pnl, 0);
const grossLoss = Math.abs(losers.reduce((sum, trade) => sum + trade.net_pnl, 0));
const winRate = closed.length ? (winners.length / closed.length) * 100 : 0;
const profitFactor = grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0;
const avgWin = winners.length ? grossWin / winners.length : 0;
const avgLoss = losers.length ? grossLoss / losers.length : 0;
const netPnl = trades.reduce((sum, trade) => sum + trade.net_pnl, 0);

const ordered = [...trades].sort((a, b) => a.opened.localeCompare(b.opened));
let running = 0;
const cumulative = ordered.map((trade) => {
  running += trade.net_pnl;
  return running;
});
let peak = -Infinity;
let maxDrawdown = 0;
for (const value of cumulative) {
  peak = Math.max(peak, value);
  maxDrawdown = Math.max(maxDrawdown, peak - value);
}

let streak = 0;
for (let index = closed.length - 1; index >= 0; index -= 1) {
  if ((closed[index].net_pnl > 0) === (closed[closed.length - 1].net_pnl > 0)) streak += 1;
  else break;
}

const W = 640;
const H = 220;
const PAD = 26;
const minV = Math.min(0, ...cumulative);
const maxV = Math.max(...cumulative, 1);
const span = maxV - minV || 1;
const xPos = (index: number) =>
  PAD + (index / Math.max(cumulative.length - 1, 1)) * (W - PAD * 2);
const yPos = (value: number) => H - PAD - ((value - minV) / span) * (H - PAD * 2);
const points = cumulative
  .map((value, index) => `${xPos(index).toFixed(1)},${yPos(value).toFixed(1)}`)
  .join(" ");
const area = `${PAD},${H - PAD} ${points} ${W - PAD},${H - PAD}`;

export default function TradesPage() {
  return (
    <main className="!max-w-[1180px]">
      <nav>
        <div className="brand">
          <span>θ</span> ThetaForge <small>PUBLIC TRADE JOURNAL</small>
        </div>
        <div className="nav-right">
          <span className="journal-chip">PAPER ONLY</span>
          <Link className="terminal-link" href="/">
            Personal terminal →
          </Link>
        </div>
      </nav>

      <section className="paper-banner">
        <b>PAPER TRADES</b>
        <span>
          Every position below was placed on the IBKR paper simulator. No real
          capital is involved. Performance is not indicative of future results.
        </span>
      </section>

      <section className="journal-hero">
        <p className="eyebrow">THE THETA PLAYBOOK · RECEIPTS INCLUDED</p>
        <h1>
          Trades, <em>reasoning,</em> and the numbers to back both.
        </h1>
        <p className="journal-sub">
          {trader.tagline} — {trader.name} ({trader.handle}) · journal updated {asOf}.
          Every idea shows the setup, the structure, the thesis, and what actually
          happened.
        </p>
        <div className="journal-actions">
          <a
            href="https://github.com/Jadax/ThetaForge"
            target="_blank"
            rel="noreferrer"
            className="follow-button"
          >
            Follow the journal
          </a>
          <a
            href="https://github.com/Jadax/ThetaForge/tree/main/docs/STEALING_POLICY.md"
            target="_blank"
            rel="noreferrer"
            className="ghost-button"
          >
            How the signals are built
          </a>
        </div>
      </section>

      <section className="metric-strip">
        <div>
          <small>NET P&amp;L</small>
          <b className={netPnl >= 0 ? "win" : "loss"}>{usd(netPnl)}</b>
          <span>{trades.length} trades logged</span>
        </div>
        <div>
          <small>WIN RATE</small>
          <b>{winRate.toFixed(1)}%</b>
          <span>{winners.length} of {closed.length} closed</span>
        </div>
        <div>
          <small>PROFIT FACTOR</small>
          <b>{Number.isFinite(profitFactor) ? profitFactor.toFixed(2) : "—"}</b>
          <span>gross wins / gross losses</span>
        </div>
        <div>
          <small>AVG WIN</small>
          <b className="win">{usd(avgWin)}</b>
          <span>vs avg loss {usd(avgLoss)}</span>
        </div>
        <div>
          <small>MAX DRAWDOWN</small>
          <b className="loss">-{usd(maxDrawdown).slice(1)}</b>
          <span>peak-to-trough</span>
        </div>
        <div>
          <small>CURRENT STREAK</small>
          <b>{closed.length ? `${streak} ${closed[closed.length - 1].net_pnl > 0 ? "wins" : "losses"}` : "—"}</b>
          <span>consecutive closed trades</span>
        </div>
      </section>

      <section className="curve-panel">
        <div>
          <p className="eyebrow">EQUITY CURVE</p>
          <h2>Net P&amp;L, trade by trade</h2>
          <p>
            Chronological, uncompressed. The green line is real simulator fills;
            the dashed line is breakeven. Paper trading has no slippage, no fills
            you didn&apos;t get, and no capital you can lose.
          </p>
        </div>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="curve-svg"
          role="img"
          aria-label={`Cumulative net P&L curve ending at ${usd(netPnl)}`}
        >
          <line
            x1={PAD}
            y1={yPos(0)}
            x2={W - PAD}
            y2={yPos(0)}
            stroke="#26322e"
            strokeDasharray="4 4"
          />
          <polygon points={area} fill="rgba(201,255,93,0.12)" />
          <polyline
            points={points}
            fill="none"
            stroke="#c9ff5d"
            strokeWidth="3"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          {cumulative.map((value, index) => (
            <circle
              key={`${index}-${value}`}
              cx={xPos(index)}
              cy={yPos(value)}
              r="3.5"
              fill="#101714"
              stroke="#c9ff5d"
              strokeWidth="2"
            />
          ))}
          <text x={W - PAD} y={yPos(maxV) - 6} fill="#8f9995" fontSize="11" textAnchor="end">
            {usd(maxV)}
          </text>
          <text x={W - PAD} y={yPos(minV) + 14} fill="#8f9995" fontSize="11" textAnchor="end">
            {usd(minV)}
          </text>
        </svg>
      </section>

      <section className="journal-list">
        <div className="journal-list-head">
          <div>
            <p className="eyebrow">THE JOURNAL</p>
            <h2>Every trade, with the why</h2>
          </div>
          <span className="sort-note">Newest first · {trades.length} entries</span>
        </div>

        {ordered
          .slice()
          .reverse()
          .map((trade) => {
            const won = trade.net_pnl > 0;
            const statusLabel = trade.status === "open" ? "OPEN" : won ? "CLOSED · WIN" : "CLOSED · LOSS";
            return (
              <article className="journal-card" key={trade.id}>
                <div className="journal-card-head">
                  <div className="journal-symbol-row">
                    <h3>{trade.symbol}</h3>
                    <span className={`status-pill ${trade.status === "open" ? "open" : won ? "win" : "loss"}`}>
                      {statusLabel}
                    </span>
                    <span className="status-pill paper">PAPER</span>
                  </div>
                  <div className={`journal-pnl ${won ? "win" : "loss"}`}>
                    <small>NET P&amp;L</small>
                    <b>{usd(trade.net_pnl)}</b>
                    <span>{pct(trade.net_pnl_pct)} of max profit</span>
                  </div>
                </div>

                <p className="journal-strategy">
                  {strategyLabel(trade.strategy)}
                  {trade.dte_at_entry ? ` · ${trade.dte_at_entry} DTE at entry` : ""}
                  {" · "}IVR {trade.entry_ivr} at entry
                </p>

                <div className="journal-legs">
                  {trade.legs.map((leg, index) => (
                    <span key={`${trade.id}-leg-${index}`} className={leg.action === "SELL" ? "sell" : "buy"}>
                      {leg.action} {leg.type} {leg.strike ?? ""}
                      {leg.expiry ? ` · ${leg.expiry}` : ""}
                      {leg.dte ? ` · ${leg.dte}d` : ""}
                    </span>
                  ))}
                </div>

                <div className="journal-meta">
                  <span><small>OPENED</small><b>{dateLabel(trade.opened)}</b></span>
                  <span><small>{trade.status === "open" ? "CURRENT" : "CLOSED"}</small><b>{trade.closed ? dateLabel(trade.closed) : "—"}</b></span>
                  <span><small>CAPITAL AT RISK</small><b>{usd(trade.capital_at_risk).slice(1)}</b></span>
                  <span><small>MAX PROFIT</small><b>{usd(trade.max_profit).slice(1)}</b></span>
                </div>

                <div className="journal-thesis">
                  <small>THE WHY</small>
                  <p>{trade.reason}</p>
                </div>

                <div className="journal-exit">
                  <small>WHAT HAPPENED</small>
                  <p>{trade.exit_note}</p>
                </div>

                <div className="journal-footer">
                  <div className="journal-tags">
                    {trade.tags.map((tag) => (
                      <span key={tag}>#{tag}</span>
                    ))}
                  </div>
                  <div className="journal-links">
                    {trade.research.map((link) => (
                      <a key={link.label} href={link.url} target="_blank" rel="noreferrer">
                        {link.label} ↗
                      </a>
                    ))}
                    <span className="journal-receipt">
                      receipt {trade.timestamp}
                    </span>
                  </div>
                </div>
              </article>
            );
          })}
      </section>

      <footer>
        Made with {"\u2665"} by <b>{trader.name}</b> ·{" "}
        <span>Astraiva</span> · Paper-only simulator journal. This is not
        financial advice and nothing here is a solicitation to buy or sell
        securities.
      </footer>
    </main>
  );
}
