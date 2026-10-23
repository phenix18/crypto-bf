import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { API_BASE, useLanguage } from './appShared'
import { LABELS, hasCouncil, stageLabel } from './autotraderLabels'
import { type ChartPayload, drawChart } from './autotraderChart'
import './autotrader.css'

const INTERVALS = ['1m', '5m', '15m', '1h', '4h']
const POLL_MS = 15000

type Mode = {
  data_source: string
  broker: string
  paper: boolean
  testnet: boolean
  live: boolean
  auto_trade: boolean
  model: string
  model_available: boolean
}

type Position = {
  symbol: string
  direction: string
  entry: number
  stop_loss: number
  take_profit: number
  quantity: number
  confluence: number
  regime: string
}

type Portfolio = {
  equity: number
  starting_equity: number
  peak_equity: number
  realised_pnl_today: number
  total_pnl: number
  drawdown_percent: number
  consecutive_losses: number
  kill_switch: boolean
  open_positions: Position[]
  closed_trades: number
}

type Status = {
  mode: Mode
  watchlist: string[]
  interval: string
  portfolio: Portfolio
  closed_trades: { outcome: string; r_multiple: number; pnl: number; reason: string; position: Position }[]
  history_size: number
}

type Opinion = { role: string; stance: string; confidence: number; rationale: string; source: string }

type Decision = {
  symbol: string
  interval: string
  approved: boolean
  stage: string
  reasons: string[]
  proposal?: ChartPayload['proposal']
  risk?: { approved: boolean; quantity: number; notional: number; risk_amount: number; reasons: string[] }
  calibration?: { allowed: boolean; basis: string; probability: number | null; reasons: string[] }
  council?: { approved: boolean; conviction: number; opinions: Opinion[]; reasons: string[] }
  quantity: number
  orders: { side: string; status: string; quantity: number; average_price: number | null }[]
}

function money(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function signed(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${money(value, digits)}`
}

/** The gauge. Colour carries the verdict, so it has to agree with the engine. */
function ConfluenceGauge({ value, gate, label }: { value: number; gate: number; label: string }) {
  const clamped = Math.max(0, Math.min(100, value))
  const radius = 52
  const circumference = Math.PI * radius
  const filled = (clamped / 100) * circumference
  const tone = clamped >= gate ? 'good' : clamped >= gate * 0.75 ? 'warn' : 'bad'
  return (
    <div className={`at-gauge at-gauge--${tone}`}>
      <svg viewBox="0 0 140 86" role="img" aria-label={`${label} ${clamped.toFixed(0)}%`}>
        <defs>
          <linearGradient id="at-gauge-fill" x1="0" x2="1">
            <stop offset="0%" stopColor="var(--at-gauge-from)" />
            <stop offset="100%" stopColor="var(--at-gauge-to)" />
          </linearGradient>
        </defs>
        <path d="M 18 74 A 52 52 0 0 1 122 74" className="at-gauge-track" />
        <path
          d="M 18 74 A 52 52 0 0 1 122 74"
          className="at-gauge-value"
          strokeDasharray={`${filled} ${circumference}`}
        />
        <line
          className="at-gauge-gate"
          x1={70 - radius * Math.cos((gate / 100) * Math.PI)}
          y1={74 - radius * Math.sin((gate / 100) * Math.PI)}
          x2={70 - (radius - 11) * Math.cos((gate / 100) * Math.PI)}
          y2={74 - (radius - 11) * Math.sin((gate / 100) * Math.PI)}
        />
      </svg>
      <div className="at-gauge-centre">
        <strong>{clamped.toFixed(0)}</strong>
        <span>{label}</span>
      </div>
    </div>
  )
}

export function AutoTraderPage() {
  const { language } = useLanguage()
  const L = LABELS[language] ?? LABELS.en

  const [status, setStatus] = useState<Status | null>(null)
  const [chart, setChart] = useState<ChartPayload | null>(null)
  const [decision, setDecision] = useState<Decision | null>(null)
  const [gate, setGate] = useState(65)
  const [audit, setAudit] = useState<Record<string, unknown>[]>([])
  const [symbol, setSymbol] = useState('BTCUSDT')
  const [interval, setIntervalValue] = useState('5m')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  const call = useCallback(async (path: string, init?: RequestInit) => {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload?.detail || `${response.status} ${response.statusText}`)
    }
    return payload
  }, [])

  const loadStatus = useCallback(async () => {
    try {
      const payload: Status = await call('/auto/status')
      setStatus(payload)
      if (payload.watchlist?.length && !payload.watchlist.includes(symbol)) {
        setSymbol(payload.watchlist[0])
      }
      setError(null)
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc))
    }
  }, [call, symbol])

  const loadChart = useCallback(async () => {
    try {
      const payload: ChartPayload = await call(
        `/auto/chart?symbol=${encodeURIComponent(symbol)}&interval=${interval}`,
      )
      setChart(payload)
      setError(null)
    } catch (exc) {
      setChart(null)
      setError(String(exc instanceof Error ? exc.message : exc))
    }
  }, [call, symbol, interval])

  const loadSupporting = useCallback(async () => {
    try {
      const [calibration, trail] = await Promise.all([call('/auto/calibration'), call('/auto/audit?limit=25')])
      if (typeof calibration?.config?.confluence_gate === 'number') {
        setGate(calibration.config.confluence_gate)
      }
      setAudit(Array.isArray(trail?.entries) ? trail.entries : [])
    } catch {
      /* the trail is a convenience; losing it must not blank the screen */
    }
  }, [call])

  useEffect(() => {
    loadStatus()
    loadSupporting()
    const timer = window.setInterval(() => {
      loadStatus()
      loadSupporting()
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [loadStatus, loadSupporting])

  useEffect(() => {
    loadChart()
    const timer = window.setInterval(loadChart, POLL_MS)
    return () => window.clearInterval(timer)
  }, [loadChart])

  // Redraw on new data and on resize: a chart that keeps a stale width draws
  // the levels in the wrong place after a window change.
  useEffect(() => {
    if (!canvasRef.current || !chart) return
    const canvas = canvasRef.current
    const render = () => drawChart(canvas, chart)
    render()
    const observer = new ResizeObserver(render)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [chart])

  const act = async (name: string, path: string, body?: unknown) => {
    setBusy(name)
    setError(null)
    try {
      const payload = await call(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
      if (name === 'analyse' || name === 'trade') setDecision(payload as Decision)
      await Promise.all([loadStatus(), loadChart(), loadSupporting()])
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc))
    } finally {
      setBusy(null)
    }
  }

  const proposal = decision?.proposal || chart?.proposal
  const mode = status?.mode
  const portfolio = status?.portfolio
  const council = hasCouncil(decision?.council) ? decision?.council : null

  const directionTone = useMemo(() => {
    if (!proposal || proposal.direction === 'flat') return 'flat'
    return proposal.direction === 'long' ? 'long' : 'short'
  }, [proposal])

  return (
    <div className="at-page">
      <header className="at-head">
        <div>
          <h1>{L.title}</h1>
          <p>{L.subtitle}</p>
        </div>
        <div className="at-badges">
          {mode && (
            <>
              <span className={`at-badge ${mode.live ? 'at-badge--live' : 'at-badge--safe'}`}>
                {mode.paper ? 'PAPER' : mode.live ? 'LIVE' : 'TESTNET'}
              </span>
              <span className="at-badge at-badge--muted">{mode.data_source}</span>
              <span className={`at-badge ${mode.auto_trade ? 'at-badge--armed' : 'at-badge--muted'}`}>
                {mode.auto_trade ? 'AUTO ON' : 'AUTO OFF'}
              </span>
              <span className="at-badge at-badge--muted">
                {mode.model_available ? mode.model : 'no model'}
              </span>
            </>
          )}
        </div>
      </header>

      {mode && !mode.auto_trade && <div className="at-notice">{L.disarmed}</div>}
      {chart?.source === 'synthetic' && <div className="at-notice at-notice--warn">{L.demo}</div>}
      {error && <div className="at-notice at-notice--error">{error}</div>}

      <div className="at-controls">
        <div className="at-chips">
          {(status?.watchlist || [symbol]).map((item) => (
            <button
              key={item}
              className={`at-chip ${item === symbol ? 'is-active' : ''}`}
              onClick={() => setSymbol(item)}
            >
              {item}
            </button>
          ))}
        </div>
        <div className="at-chips">
          {INTERVALS.map((item) => (
            <button
              key={item}
              className={`at-chip ${item === interval ? 'is-active' : ''}`}
              onClick={() => setIntervalValue(item)}
            >
              {item}
            </button>
          ))}
        </div>
        <div className="at-actions">
          <button
            className="at-btn at-btn--ghost"
            disabled={busy !== null}
            onClick={() => act('analyse', '/auto/analyse', { symbol, interval })}
          >
            {busy === 'analyse' ? '…' : L.analyse}
          </button>
          <button
            className="at-btn at-btn--go"
            disabled={busy !== null || !mode?.auto_trade}
            title={mode?.auto_trade ? undefined : L.disarmed}
            onClick={() => act('trade', '/auto/trade', { symbol, interval })}
          >
            {busy === 'trade' ? '…' : L.trade}
          </button>
          <button
            className={`at-btn ${portfolio?.kill_switch ? 'at-btn--warn' : 'at-btn--stop'}`}
            disabled={busy !== null}
            onClick={() =>
              act('kill', '/auto/kill-switch', {
                engaged: !portfolio?.kill_switch,
                confirm: portfolio?.kill_switch ? 'release' : '',
              })
            }
          >
            {portfolio?.kill_switch ? L.killOff : L.killOn}
          </button>
        </div>
      </div>

      <div className="at-grid">
        <section className="at-card at-card--chart">
          <div className="at-card-head">
            <h2>
              {symbol} · {interval}
              <span className={`at-dir at-dir--${directionTone}`}>
                {proposal ? proposal.direction.toUpperCase() : '—'}
              </span>
            </h2>
            <div className="at-legend">
              <span className="at-key at-key--fast">EMA9</span>
              <span className="at-key at-key--slow">EMA21</span>
              <span className="at-key at-key--trend">EMA50</span>
              <span className="at-key at-key--band">BB20</span>
              <span className="at-key at-key--stop">SL</span>
              <span className="at-key at-key--target">TP</span>
            </div>
          </div>
          <canvas ref={canvasRef} className="at-canvas" />
        </section>

        <aside className="at-side">
          <section className="at-card">
            <ConfluenceGauge value={proposal?.confluence ?? 0} gate={gate} label={L.confluence} />
            <dl className="at-stats">
              <div>
                <dt>{L.participation}</dt>
                <dd>{proposal?.participation !== undefined ? `${proposal.participation}%` : '—'}</dd>
              </div>
              <div>
                <dt>{L.regime}</dt>
                <dd>{proposal?.regime ?? '—'}</dd>
              </div>
              <div>
                <dt>{L.riskReward}</dt>
                <dd>{proposal?.risk_reward ? `${proposal.risk_reward.toFixed(2)} : 1` : '—'}</dd>
              </div>
              {decision && (
                <div>
                  <dt>{L.stage}</dt>
                  <dd className={`at-stage at-stage--${decision.stage}`}>
                    {stageLabel(decision.stage, language)}
                  </dd>
                </div>
              )}
            </dl>
            {(decision?.reasons || proposal?.blockers || []).length > 0 && (
              <ul className="at-reasons">
                {(decision?.reasons || proposal?.blockers || []).slice(0, 4).map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            )}
            {!decision && proposal && !proposal.actionable && proposal.blockers.length === 0 && (
              <p className="at-muted">{L.nothing}</p>
            )}
          </section>

          {council && (
            <section className="at-card">
              <div className="at-card-head">
                <h2>{L.council}</h2>
                <span className="at-conviction">
                  {L.conviction} {((council.conviction ?? 0) * 100).toFixed(0)}%
                </span>
              </div>
              <ul className="at-council">
                {council.opinions.map((opinion) => (
                  <li key={opinion.role}>
                    <span className={`at-stance at-stance--${opinion.stance}`}>{opinion.stance}</span>
                    <div>
                      <strong>{opinion.role}</strong>
                      <p>{opinion.rationale}</p>
                      <small>
                        {opinion.source} · {(opinion.confidence * 100).toFixed(0)}%
                      </small>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="at-card">
            <div className="at-card-head">
              <h2>{L.balance}</h2>
            </div>
            <dl className="at-stats at-stats--wide">
              <div>
                <dt>{L.equity}</dt>
                <dd>{money(portfolio?.equity)}</dd>
              </div>
              <div>
                <dt>{L.pnl}</dt>
                <dd className={(portfolio?.total_pnl ?? 0) >= 0 ? 'at-up' : 'at-down'}>
                  {signed(portfolio?.total_pnl)}
                </dd>
              </div>
              <div>
                <dt>{L.today}</dt>
                <dd className={(portfolio?.realised_pnl_today ?? 0) >= 0 ? 'at-up' : 'at-down'}>
                  {signed(portfolio?.realised_pnl_today)}
                </dd>
              </div>
              <div>
                <dt>{L.drawdown}</dt>
                <dd className={(portfolio?.drawdown_percent ?? 0) > 5 ? 'at-down' : ''}>
                  {money(portfolio?.drawdown_percent)}%
                </dd>
              </div>
            </dl>
            <div className="at-positions">
              <h3>{L.positions}</h3>
              {portfolio?.open_positions?.length ? (
                portfolio.open_positions.map((item) => (
                  <div key={item.symbol} className="at-position">
                    <span className={`at-dir at-dir--${item.direction}`}>{item.direction}</span>
                    <strong>{item.symbol}</strong>
                    <span className="at-muted">
                      {money(item.quantity, 5)} @ {money(item.entry)}
                    </span>
                    <span className="at-sl">SL {money(item.stop_loss)}</span>
                    <span className="at-tp">TP {money(item.take_profit)}</span>
                    <button
                      className="at-btn at-btn--tiny"
                      disabled={busy !== null}
                      onClick={() => act('close', '/auto/close', { symbol: item.symbol, price: item.entry })}
                    >
                      {L.close}
                    </button>
                  </div>
                ))
              ) : (
                <p className="at-muted">{L.noPositions}</p>
              )}
            </div>
          </section>
        </aside>
      </div>

      <div className="at-grid at-grid--bottom">
        <section className="at-card">
          <div className="at-card-head">
            <h2>{L.votes}</h2>
          </div>
          <ul className="at-votes">
            {(proposal?.votes || []).map((vote) => (
              <li key={vote.name}>
                <span className={`at-vote at-vote--${vote.direction}`}>{vote.name}</span>
                <div className="at-bar">
                  <div
                    className={`at-bar-fill at-bar-fill--${vote.direction}`}
                    style={{ width: `${Math.round(Math.min(1, vote.strength) * 100)}%` }}
                  />
                </div>
                <small>{vote.detail}</small>
              </li>
            ))}
          </ul>
        </section>

        <section className="at-card">
          <div className="at-card-head">
            <h2>{L.readings}</h2>
          </div>
          <dl className="at-readings">
            {Object.entries(proposal?.readings || {}).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{typeof value === 'number' ? money(value, 4) : String(value ?? '—')}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="at-card">
          <div className="at-card-head">
            <h2>{L.audit}</h2>
          </div>
          <ul className="at-audit">
            {audit.slice(0, 12).map((entry, index) => (
              <li key={index}>
                <span className="at-audit-kind">{String(entry.kind ?? '')}</span>
                <span>{String(entry.symbol ?? '')}</span>
                <span className={`at-stage at-stage--${String(entry.stage ?? '')}`}>
                  {entry.stage
                    ? stageLabel(String(entry.stage), language)
                    : String(entry.outcome ?? '')}
                </span>
                <small>{Array.isArray(entry.reasons) ? String(entry.reasons[0] ?? '') : ''}</small>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}

export default AutoTraderPage
