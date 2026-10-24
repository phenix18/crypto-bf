/**
 * Canvas drawing for the auto-trader chart.
 *
 * Hand-drawn rather than handed to a charting library for one reason: the
 * overlays are the point. Entry, stop and target have to sit on exactly the
 * price scale the candles were drawn on, and the vote colours have to match
 * the engine's own verdict. A generic library would do the candles well and
 * fight us on everything else.
 *
 * Everything here is pure: same payload and same size in, same pixels out. No
 * fetching, no state, no reading the clock — so a screenshot of a bug can be
 * reproduced from the payload alone.
 */

export type Candle = { t: number; o: number; h: number; l: number; c: number; v: number }

export type Level = { price: number; touches: number }

export type Vote = {
  name: string
  direction: string
  strength: number
  weight: number
  detail: string
}

export type Proposal = {
  symbol: string
  interval: string
  direction: string
  regime: string
  confluence: number
  participation?: number
  entry: number
  stop_loss: number | null
  take_profit: number | null
  risk_reward: number | null
  actionable: boolean
  blockers: string[]
  votes: Vote[]
  readings: Record<string, number | string | null>
}

export type ChartPayload = {
  symbol: string
  interval: string
  source: string
  generated_at: string
  candles: Candle[]
  indicators: Record<string, (number | null)[]>
  levels: { support: Level[]; resistance: Level[] }
  proposal: Proposal
}

export const PALETTE = {
  up: '#16c784',
  upSoft: 'rgba(22, 199, 132, 0.18)',
  down: '#ea3943',
  downSoft: 'rgba(234, 57, 67, 0.18)',
  grid: 'rgba(148, 163, 184, 0.12)',
  axis: 'rgba(148, 163, 184, 0.65)',
  emaFast: '#38bdf8',
  emaSlow: '#a78bfa',
  emaTrend: '#f59e0b',
  band: 'rgba(96, 165, 250, 0.10)',
  bandLine: 'rgba(96, 165, 250, 0.45)',
  support: 'rgba(22, 199, 132, 0.55)',
  resistance: 'rgba(234, 57, 67, 0.55)',
  stop: '#ef4444',
  target: '#22c55e',
  entry: '#e2e8f0',
  rsi: '#f472b6',
  macd: '#38bdf8',
  signal: '#fbbf24',
  text: '#cbd5e1',
  textDim: 'rgba(203, 213, 225, 0.55)',
}

type Pane = { top: number; height: number }

type Layout = {
  width: number
  height: number
  left: number
  right: number
  price: Pane
  volume: Pane
  rsi: Pane
  macd: Pane
}

function layoutFor(width: number, height: number): Layout {
  const left = 8
  const right = 62 // room for the price axis, which lives on the right
  const usable = height - 24
  const price = { top: 8, height: usable * 0.56 }
  const volume = { top: price.top + price.height + 6, height: usable * 0.1 }
  const rsi = { top: volume.top + volume.height + 10, height: usable * 0.15 }
  const macd = { top: rsi.top + rsi.height + 10, height: usable * 0.15 }
  return { width, height, left, right, price, volume, rsi, macd }
}

function extent(values: number[]): [number, number] {
  let low = Infinity
  let high = -Infinity
  for (const value of values) {
    if (!Number.isFinite(value)) continue
    if (value < low) low = value
    if (value > high) high = value
  }
  if (low === Infinity) return [0, 1]
  if (low === high) return [low * 0.999, high * 1.001]
  return [low, high]
}

function scaler(pane: Pane, low: number, high: number) {
  const span = high - low || 1
  return (value: number) => pane.top + pane.height - ((value - low) / span) * pane.height
}

function formatPrice(value: number): string {
  if (!Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1000) return value.toFixed(0)
  if (abs >= 1) return value.toFixed(2)
  if (abs >= 0.01) return value.toFixed(4)
  return value.toFixed(6)
}

function line(
  ctx: CanvasRenderingContext2D,
  series: (number | null)[],
  x: (index: number) => number,
  y: (value: number) => number,
  colour: string,
  width = 1.4,
) {
  ctx.save()
  ctx.strokeStyle = colour
  ctx.lineWidth = width
  ctx.lineJoin = 'round'
  ctx.beginPath()
  let drawing = false
  series.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) {
      drawing = false
      return
    }
    const px = x(index)
    const py = y(value)
    if (!drawing) {
      ctx.moveTo(px, py)
      drawing = true
    } else {
      ctx.lineTo(px, py)
    }
  })
  ctx.stroke()
  ctx.restore()
}

function dashed(
  ctx: CanvasRenderingContext2D,
  y: number,
  from: number,
  to: number,
  colour: string,
  dash: number[] = [5, 5],
) {
  ctx.save()
  ctx.strokeStyle = colour
  ctx.lineWidth = 1
  ctx.setLineDash(dash)
  ctx.beginPath()
  ctx.moveTo(from, y)
  ctx.lineTo(to, y)
  ctx.stroke()
  ctx.restore()
}

function tag(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  text: string,
  background: string,
  colour = '#0b1120',
) {
  ctx.save()
  ctx.font = '600 10px ui-monospace, SFMono-Regular, Menlo, monospace'
  const width = ctx.measureText(text).width + 10
  const height = 15
  const top = Math.min(Math.max(y - height / 2, 1), ctx.canvas.height - height - 1)
  ctx.fillStyle = background
  ctx.beginPath()
  const radius = 3
  ctx.roundRect?.(x, top, width, height, radius)
  if (!ctx.roundRect) ctx.rect(x, top, width, height)
  ctx.fill()
  ctx.fillStyle = colour
  ctx.fillText(text, x + 5, top + 11)
  ctx.restore()
}

/** Prepare a canvas for the device's pixel ratio and hand back its context. */
export function prepare(canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
  const ratio = window.devicePixelRatio || 1
  const width = canvas.clientWidth || 640
  const height = canvas.clientHeight || 420
  canvas.width = Math.floor(width * ratio)
  canvas.height = Math.floor(height * ratio)
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
  ctx.clearRect(0, 0, width, height)
  return ctx
}

export function drawChart(canvas: HTMLCanvasElement, payload: ChartPayload): void {
  const ctx = prepare(canvas)
  if (!ctx) return

  const width = canvas.clientWidth || 640
  const height = canvas.clientHeight || 420
  const l = layoutFor(width, height)
  const candles = payload.candles
  if (!candles.length) {
    ctx.fillStyle = PALETTE.textDim
    ctx.font = '13px system-ui, sans-serif'
    ctx.fillText('Aucune donnée', 16, 28)
    return
  }

  const plotWidth = l.width - l.left - l.right
  const step = plotWidth / candles.length
  const bodyWidth = Math.max(1.2, Math.min(9, step * 0.62))
  const x = (index: number) => l.left + step * (index + 0.5)

  // The price pane has to hold the candles and every overlay drawn on them,
  // targets included: a stop outside the visible range is a stop nobody checks.
  const priceValues: number[] = []
  for (const candle of candles) {
    priceValues.push(candle.h, candle.l)
  }
  for (const key of ['bb_upper', 'bb_lower', 'ema_trend']) {
    for (const value of payload.indicators[key] || []) {
      if (value !== null && Number.isFinite(value)) priceValues.push(value)
    }
  }
  const proposal = payload.proposal
  for (const value of [proposal?.stop_loss, proposal?.take_profit]) {
    if (typeof value === 'number' && Number.isFinite(value)) priceValues.push(value)
  }
  const [rawLow, rawHigh] = extent(priceValues)
  const pad = (rawHigh - rawLow) * 0.06
  const priceY = scaler(l.price, rawLow - pad, rawHigh + pad)

  // The trade's own labels own the axis gutter: an axis tick printed over the
  // stop price is worse than a missing tick, because the reader cannot tell
  // which number is the stop.
  const reserved: number[] = []
  for (const value of [proposal?.entry, proposal?.stop_loss, proposal?.take_profit]) {
    if (typeof value === 'number' && Number.isFinite(value)) reserved.push(priceY(value))
  }

  // --- grid and price axis
  ctx.save()
  ctx.font = '10px ui-monospace, monospace'
  for (let i = 0; i <= 4; i += 1) {
    const value = rawLow - pad + ((rawHigh - rawLow + pad * 2) * i) / 4
    const y = priceY(value)
    ctx.strokeStyle = PALETTE.grid
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(l.left, y)
    ctx.lineTo(l.width - l.right, y)
    ctx.stroke()
    if (!reserved.some((taken) => Math.abs(taken - y) < 11)) {
      ctx.fillStyle = PALETTE.axis
      ctx.fillText(formatPrice(value), l.width - l.right + 6, y + 3)
    }
  }
  ctx.restore()

  // --- Bollinger envelope, drawn first so it sits behind everything
  const upper = payload.indicators.bb_upper || []
  const lower = payload.indicators.bb_lower || []
  if (upper.length === candles.length) {
    ctx.save()
    ctx.fillStyle = PALETTE.band
    ctx.beginPath()
    let started = false
    upper.forEach((value, index) => {
      if (value === null) return
      const px = x(index)
      const py = priceY(value)
      if (!started) {
        ctx.moveTo(px, py)
        started = true
      } else ctx.lineTo(px, py)
    })
    for (let index = lower.length - 1; index >= 0; index -= 1) {
      const value = lower[index]
      if (value === null) continue
      ctx.lineTo(x(index), priceY(value))
    }
    ctx.closePath()
    ctx.fill()
    ctx.restore()
    line(ctx, upper, x, priceY, PALETTE.bandLine, 1)
    line(ctx, lower, x, priceY, PALETTE.bandLine, 1)
  }

  // --- structure levels
  for (const level of payload.levels?.support || []) {
    dashed(ctx, priceY(level.price), l.left, l.width - l.right, PALETTE.support, [2, 6])
  }
  for (const level of payload.levels?.resistance || []) {
    dashed(ctx, priceY(level.price), l.left, l.width - l.right, PALETTE.resistance, [2, 6])
  }

  // --- candles
  candles.forEach((candle, index) => {
    const rising = candle.c >= candle.o
    const colour = rising ? PALETTE.up : PALETTE.down
    const px = x(index)
    ctx.strokeStyle = colour
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(px, priceY(candle.h))
    ctx.lineTo(px, priceY(candle.l))
    ctx.stroke()

    const openY = priceY(candle.o)
    const closeY = priceY(candle.c)
    const top = Math.min(openY, closeY)
    const bodyHeight = Math.max(1, Math.abs(closeY - openY))
    const gradient = ctx.createLinearGradient(px - bodyWidth / 2, top, px + bodyWidth / 2, top)
    gradient.addColorStop(0, rising ? '#0e9f6a' : '#c22630')
    gradient.addColorStop(0.5, colour)
    gradient.addColorStop(1, rising ? '#0e9f6a' : '#c22630')
    ctx.fillStyle = gradient
    ctx.fillRect(px - bodyWidth / 2, top, bodyWidth, bodyHeight)
  })

  // --- moving averages
  line(ctx, payload.indicators.ema_fast || [], x, priceY, PALETTE.emaFast, 1.5)
  line(ctx, payload.indicators.ema_slow || [], x, priceY, PALETTE.emaSlow, 1.5)
  line(ctx, payload.indicators.ema_trend || [], x, priceY, PALETTE.emaTrend, 1.5)

  // --- the trade: entry, stop, target
  if (proposal) {
    const entryY = priceY(proposal.entry)
    const stop = proposal.stop_loss
    const target = proposal.take_profit

    // The stop and the target own their labels; the entry gives way when the
    // three would collide. On a trading screen a half-covered number is worse
    // than no number, because the reader cannot tell which level it belongs to.
    const crowded = [stop, target].some(
      (value) => typeof value === 'number' && Math.abs(priceY(value) - entryY) < 17,
    )
    dashed(ctx, entryY, l.left, l.width - l.right, PALETTE.entry, [6, 4])
    if (!crowded) {
      tag(ctx, l.width - l.right + 2, entryY, formatPrice(proposal.entry), PALETTE.entry)
    }
    if (typeof stop === 'number') {
      const stopY = priceY(stop)
      ctx.save()
      ctx.fillStyle = PALETTE.downSoft
      ctx.fillRect(l.left, Math.min(entryY, stopY), plotWidth, Math.abs(stopY - entryY))
      ctx.restore()
      dashed(ctx, stopY, l.left, l.width - l.right, PALETTE.stop, [4, 3])
      tag(ctx, l.width - l.right + 2, stopY, `SL ${formatPrice(stop)}`, PALETTE.stop, '#fff')
    }
    if (typeof target === 'number') {
      const targetY = priceY(target)
      ctx.save()
      ctx.fillStyle = PALETTE.upSoft
      ctx.fillRect(l.left, Math.min(entryY, targetY), plotWidth, Math.abs(targetY - entryY))
      ctx.restore()
      dashed(ctx, targetY, l.left, l.width - l.right, PALETTE.target, [4, 3])
      tag(ctx, l.width - l.right + 2, targetY, `TP ${formatPrice(target)}`, PALETTE.target, '#06230f')
    }
  }

  // --- volume
  const volumes = candles.map((c) => c.v)
  const [, volumeHigh] = extent(volumes)
  const volumeY = scaler(l.volume, 0, volumeHigh)
  candles.forEach((candle, index) => {
    const rising = candle.c >= candle.o
    ctx.fillStyle = rising ? PALETTE.upSoft : PALETTE.downSoft
    const top = volumeY(candle.v)
    ctx.fillRect(x(index) - bodyWidth / 2, top, bodyWidth, l.volume.top + l.volume.height - top)
  })

  // --- RSI, with the bands that make it readable at a glance
  const rsi = payload.indicators.rsi || []
  const rsiY = scaler(l.rsi, 0, 100)
  ctx.save()
  ctx.fillStyle = 'rgba(148, 163, 184, 0.06)'
  ctx.fillRect(l.left, rsiY(70), plotWidth, rsiY(30) - rsiY(70))
  ctx.restore()
  dashed(ctx, rsiY(70), l.left, l.width - l.right, PALETTE.resistance, [3, 4])
  dashed(ctx, rsiY(30), l.left, l.width - l.right, PALETTE.support, [3, 4])
  dashed(ctx, rsiY(50), l.left, l.width - l.right, PALETTE.grid, [1, 5])
  line(ctx, rsi, x, rsiY, PALETTE.rsi, 1.6)
  ctx.save()
  ctx.font = '10px ui-monospace, monospace'
  ctx.fillStyle = PALETTE.axis
  ctx.fillText('RSI 70', l.width - l.right + 6, rsiY(70) + 3)
  ctx.fillText('30', l.width - l.right + 6, rsiY(30) + 3)
  ctx.restore()

  // --- MACD
  const macd = payload.indicators.macd || []
  const signal = payload.indicators.macd_signal || []
  const histogram = payload.indicators.macd_histogram || []
  const macdValues = [...macd, ...signal, ...histogram]
    .filter((v): v is number => v !== null && Number.isFinite(v))
  const [macdLow, macdHigh] = extent(macdValues.length ? macdValues : [0, 1])
  const bound = Math.max(Math.abs(macdLow), Math.abs(macdHigh)) || 1
  const macdY = scaler(l.macd, -bound, bound)
  dashed(ctx, macdY(0), l.left, l.width - l.right, PALETTE.grid, [1, 5])
  histogram.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) return
    const zero = macdY(0)
    const top = macdY(value)
    ctx.fillStyle = value >= 0 ? PALETTE.upSoft : PALETTE.downSoft
    ctx.fillRect(x(index) - bodyWidth / 2, Math.min(zero, top), bodyWidth, Math.abs(top - zero))
  })
  line(ctx, macd, x, macdY, PALETTE.macd, 1.4)
  line(ctx, signal, x, macdY, PALETTE.signal, 1.4)
  ctx.save()
  ctx.font = '10px ui-monospace, monospace'
  ctx.fillStyle = PALETTE.axis
  ctx.fillText('MACD', l.left + 4, l.macd.top + 10)
  ctx.restore()
}
