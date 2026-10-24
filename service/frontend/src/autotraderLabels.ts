/**
 * Every label on the auto-trading desk, in one place.
 *
 * Separated from the page so the tables can be checked without mounting
 * React: the test suite asserts all three languages carry the same keys, and
 * that every stage the backend can report has a name here. A stage with no
 * label is a screen that tells the reader "council" when it meant to say why
 * the trade was refused.
 */

import type { Language } from './i18n'

export type DeskLabels = {
  title: string
  subtitle: string
  analyse: string
  trade: string
  killOn: string
  killOff: string
  confluence: string
  participation: string
  regime: string
  riskReward: string
  stage: string
  council: string
  conviction: string
  votes: string
  readings: string
  balance: string
  equity: string
  pnl: string
  today: string
  drawdown: string
  positions: string
  close: string
  audit: string
  noPositions: string
  disarmed: string
  demo: string
  nothing: string
}

export const LABELS: Record<Language, DeskLabels> = {
  zh: {
    title: '自动交易台',
    subtitle: '策略 → 风控 → 校准 → 多智能体评议 → 下单',
    analyse: '分析',
    trade: '下单',
    killOn: '启用熔断',
    killOff: '解除熔断',
    confluence: '共振度',
    participation: '参与度',
    regime: '市场状态',
    riskReward: '盈亏比',
    stage: '停在',
    council: '智能体评议',
    conviction: '信心',
    votes: '指标投票',
    readings: '指标读数',
    balance: '账户',
    equity: '净值',
    pnl: '总盈亏',
    today: '今日已实现',
    drawdown: '回撤',
    positions: '持仓',
    close: '平仓',
    audit: '审计日志',
    noPositions: '暂无持仓',
    disarmed: '自动交易未启用：设置 AUTO_TRADE_ENABLED=true 后才会真正下单',
    demo: '演示数据源，非真实行情',
    nothing: '本根K线没有可交易的形态',
  },
  en: {
    title: 'Auto-trading desk',
    subtitle: 'strategy → risk → calibration → agent council → orders',
    analyse: 'Analyse',
    trade: 'Place order',
    killOn: 'Engage kill switch',
    killOff: 'Release kill switch',
    confluence: 'Confluence',
    participation: 'Participation',
    regime: 'Regime',
    riskReward: 'Reward / risk',
    stage: 'Stopped at',
    council: 'Agent council',
    conviction: 'Conviction',
    votes: 'Indicator votes',
    readings: 'Readings',
    balance: 'Account',
    equity: 'Equity',
    pnl: 'Total P&L',
    today: 'Realised today',
    drawdown: 'Drawdown',
    positions: 'Open positions',
    close: 'Close',
    audit: 'Audit trail',
    noPositions: 'Nothing open',
    disarmed: 'Auto-trading is off: set AUTO_TRADE_ENABLED=true before orders can be placed',
    demo: 'Demonstration feed, not a live market',
    nothing: 'Nothing tradable on this bar',
  },
  fr: {
    title: 'Poste de trading automatique',
    subtitle: 'stratégie → risque → calibration → conseil d\'agents → ordres',
    analyse: 'Analyser',
    trade: 'Passer l\'ordre',
    killOn: 'Couper le robot',
    killOff: 'Relancer le robot',
    confluence: 'Confluence',
    participation: 'Participation',
    regime: 'Régime',
    riskReward: 'Gain / risque',
    stage: 'Arrêté à',
    council: 'Conseil d\'agents',
    conviction: 'Conviction',
    votes: 'Votes des indicateurs',
    readings: 'Lectures',
    balance: 'Compte',
    equity: 'Capital',
    pnl: 'Résultat total',
    today: 'Réalisé aujourd\'hui',
    drawdown: 'Baisse depuis le pic',
    positions: 'Positions ouvertes',
    close: 'Fermer',
    audit: 'Journal d\'audit',
    noPositions: 'Aucune position',
    disarmed: "Trading automatique désactivé : passez AUTO_TRADE_ENABLED=true pour qu'un ordre puisse partir",
    demo: 'Flux de démonstration, ce n\'est pas un vrai marché',
    nothing: 'Rien à trader sur cette bougie',
  },
}

export const STAGE_LABELS: Record<string, Record<Language, string>> = {
  strategy: { zh: '策略', en: 'strategy', fr: 'stratégie' },
  risk: { zh: '风控', en: 'risk', fr: 'risque' },
  calibration: { zh: '校准', en: 'calibration', fr: 'calibration' },
  council: { zh: '评议', en: 'council', fr: 'conseil' },
  approved: { zh: '通过', en: 'approved', fr: 'validé' },
  executed: { zh: '已下单', en: 'executed', fr: 'exécuté' },
  execution: { zh: '下单失败', en: 'execution', fr: 'exécution' },
  position: { zh: '已持仓', en: 'position', fr: 'position' },
  bar: { zh: '本根K线', en: 'bar', fr: 'bougie' },
  error: { zh: '错误', en: 'error', fr: 'erreur' },
}

/** A stage name in the reader's language, or the raw one if it is unknown. */
export function stageLabel(stage: string, language: Language): string {
  const labels = STAGE_LABELS[stage]
  return labels ? labels[language] ?? labels.en : stage
}


/**
 * Whether a decision actually reached the council and has opinions to show.
 *
 * Not `decision.council` on its own. A decision that stopped at an earlier
 * gate still carries `council: {}` — an empty object, which is truthy in
 * JavaScript — so testing the object rendered the panel and then threw on
 * `opinions.map`. The thing worth asking is whether there are opinions.
 */
export function hasCouncil(
  council: { opinions?: unknown } | null | undefined,
): council is { opinions: unknown[] } {
  return Array.isArray(council?.opinions) && council.opinions.length > 0
}
