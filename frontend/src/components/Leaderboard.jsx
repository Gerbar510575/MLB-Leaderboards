import { useState, useEffect, useCallback } from 'react'

// ---------------------------------------------------------------------------
// Metric metadata
// ---------------------------------------------------------------------------
const METRIC_LABELS = {
  barrel_rate:     'Barrel Rate',
  exit_velocity:   'Exit Velocity',
  hard_hit_rate:   'Hard Hit Rate',
  launch_angle:    'Launch Angle',
  p_avg_ev:        'Avg EV Against',
  p_barrel_rate:   'Barrel% Against',
  p_bb9:           'BB/9',
  p_era_diff:      'ERA − xERA',
  p_hard_hit_rate: 'Hard Hit% Against',
  p_k9:            'K/9',
  p_k_bb_diff:     'K-BB%',
  p_xera:          'xERA',
  p_xwoba_against: 'xwOBA Against',
  sprint_speed:    'Sprint Speed',
  xba:             'xBA',
  xslg:            'xSLG',
  xwoba:           'xwOBA',
  xwoba_diff:      'xwOBA − wOBA',
}

// ---------------------------------------------------------------------------
// Value formatting — add a new entry here when adding a metric
// ---------------------------------------------------------------------------
const FORMAT_CONFIG = {
  exit_velocity:   { suffix: ' mph', decimals: 1 },
  xba:             { decimals: 3, stripLeadingZero: true },  // 0.312 → .312
  xslg:            { decimals: 3, stripLeadingZero: true },
  xwoba:           { decimals: 3, stripLeadingZero: true },
  xwoba_diff:      { decimals: 3, stripLeadingZero: true, showSign: true },
  hard_hit_rate:   { suffix: '%',    decimals: 1 },
  barrel_rate:     { suffix: '%',    decimals: 1 },
  launch_angle:    { suffix: '°',    decimals: 1 },
  sprint_speed:    { suffix: ' ft/s', decimals: 1 },
  // Pitcher metrics
  p_xera:          { decimals: 2 },
  p_era_diff:      { decimals: 2, showSign: true },
  p_xwoba_against: { decimals: 3, stripLeadingZero: true },
  p_hard_hit_rate: { suffix: '%', decimals: 1 },
  p_barrel_rate:   { suffix: '%', decimals: 1 },
  p_avg_ev:        { suffix: ' mph', decimals: 1 },
  p_k9:            { decimals: 2 },
  p_bb9:           { decimals: 2 },
  p_k_bb_diff:     { suffix: '%', decimals: 2, showSign: true },
}

function formatValue(metric, value) {
  const cfg = FORMAT_CONFIG[metric] ?? { decimals: 2 }
  let str = value.toFixed(cfg.decimals)
  if (cfg.stripLeadingZero) str = str.replace(/^(-?)0\./, '$1.')  // handles negatives: -0.050 → -.050
  if (cfg.showSign && value > 0) str = '+' + str
  return str + (cfg.suffix ?? '')
}

// ---------------------------------------------------------------------------
// Percentile colour — Savant "Red Hot" convention: red = elite, blue = low
// ---------------------------------------------------------------------------
function getPercentileStyle(pct) {
  if (pct >= 90) return { backgroundColor: '#d22d2d', color: 'white' }  // deep red  — elite
  if (pct >= 70) return { backgroundColor: '#e06c6c', color: 'white' }  // light red — above avg
  if (pct >= 40) return { backgroundColor: '#e8e8e8', color: '#1f2937' } // light gray — avg (gray-800 text)
  if (pct >= 20) return { backgroundColor: '#6ba5d9', color: 'white' }  // light blue — below avg
  return               { backgroundColor: '#1a4fa0', color: 'white' }   // deep blue  — low
}

const LEGEND = [
  { label: '90–100', style: { backgroundColor: '#d22d2d', color: 'white' } },
  { label: '70–89',  style: { backgroundColor: '#e06c6c', color: 'white' } },
  { label: '40–69',  style: { backgroundColor: '#e8e8e8', color: '#1f2937' } },
  { label: '20–39',  style: { backgroundColor: '#6ba5d9', color: 'white' } },
  { label: '0–19',   style: { backgroundColor: '#1a4fa0', color: 'white' } },
]

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Scheduler status helpers
// ---------------------------------------------------------------------------
function timeAgo(isoStr) {
  if (!isoStr) return '—'
  const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000)
  if (diff < 60)   return `${diff} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`
  return `${Math.floor(diff / 86400)} 天前`
}

function formatNextRun(isoStr) {
  if (!isoStr) return '—'
  const d = new Date(isoStr)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

export default function Leaderboard() {
  const [availableMetrics, setAvailableMetrics] = useState([])
  const [metric, setMetric]     = useState('exit_velocity')
  const [limit, setLimit]       = useState(500)
  const [minReq, setMinReq]     = useState(5)
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)
  const [cacheHit, setCacheHit] = useState(null)  // true | false | null
  const [isSyncing, setIsSyncing]       = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [statusInfo, setStatusInfo]     = useState(null)

  const fetchStatus = useCallback(() => {
    Promise.all([
      fetch('/api/v1/data/status').then(r => r.json()).catch(() => null),
      fetch('/api/v1/fantasy/status').then(r => r.json()).catch(() => null),
    ]).then(([dataStatus, fantasyStatus]) => {
      if (!dataStatus) return
      setStatusInfo({
        fetchedAt:      dataStatus.fetched_at,
        nextStatsRun:   dataStatus.scheduler?.stats_refresh?.next_run,
        fantasyAt:      fantasyStatus?.synced_at,
        nextFantasyRun: dataStatus.scheduler?.fantasy_sync?.next_run,
      })
    })
  }, [])

  // Fetch available metrics on mount
  useEffect(() => {
    fetch('/api/v1/metrics')
      .then(r => r.json())
      .then(d => setAvailableMetrics(d.metrics))
      .catch(() => {})
    fetchStatus()
  }, [fetchStatus])

  const fetchLeaderboard = useCallback(() => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({
      metric_name:     metric,
      limit:           limit,
      min_requirement: minReq,
    })
    fetch(`/api/v1/leaderboard?${params}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const hit = res.headers.get('X-Cache-Hit')
        setCacheHit(hit === 'true')
        return res.json()
      })
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [metric, limit, minReq])

  // Auto-fetch whenever filter values change
  useEffect(() => { fetchLeaderboard() }, [fetchLeaderboard])

  async function handleRefreshStats() {
    setIsRefreshing(true)
    try {
      await fetch('/api/v1/data/refresh', { method: 'POST' })
      // Poll /api/v1/data/status every 2 s until done or error (max 60 s)
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 2000))
        const res = await fetch('/api/v1/data/status')
        const status = await res.json()
        if (status.refresh_job?.status === 'done') break
        if (status.refresh_job?.status === 'error') break
      }
    } catch (_) {
      // best-effort; leaderboard refresh will surface any data error
    }
    fetchLeaderboard()
    fetchStatus()
    setIsRefreshing(false)
  }

  async function handleSync() {
    setIsSyncing(true)
    try {
      await fetch('/api/v1/fantasy/sync', { method: 'POST' })
    } catch (_) {
      // best-effort; leaderboard refresh will surface any error
    }
    fetchLeaderboard()
    fetchStatus()
    setIsSyncing(false)
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6 font-sans">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            <span className="text-white">MLB</span>{' '}
            <span className="text-blue-400">Leaderboards</span>
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            Statcast Advanced Metrics — MVP Prototype
          </p>
        </div>

        {/* Cache HIT / MISS badge */}
        {cacheHit !== null && (
          <span
            className={`text-xs font-semibold px-3 py-1 rounded-full border ${
              cacheHit
                ? 'border-green-600 text-green-400 bg-green-900/30'
                : 'border-yellow-600 text-yellow-400 bg-yellow-900/30'
            }`}
          >
            {cacheHit ? 'CACHE HIT' : 'CACHE MISS'}
          </span>
        )}
      </div>

      {/* ── Filter Bar ─────────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-4 mb-5 bg-gray-900 p-4 rounded-xl border border-gray-700">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-400 uppercase tracking-wider">Metric</label>
          <select
            value={metric}
            onChange={e => setMetric(e.target.value)}
            className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
          >
            {availableMetrics.map(m => (
              <option key={m} value={m}>{METRIC_LABELS[m] ?? m}</option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-400 uppercase tracking-wider">Show Top</label>
          <input
            type="number" min={1} max={100} value={limit}
            onChange={e => setLimit(Number(e.target.value))}
            className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm w-20 focus:outline-none focus:border-blue-500"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-400 uppercase tracking-wider">Min PA</label>
          <input
            type="number" min={1} value={minReq}
            onChange={e => setMinReq(Number(e.target.value))}
            className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm w-20 focus:outline-none focus:border-blue-500"
          />
        </div>

        <div className="flex flex-col justify-end gap-2 ml-auto">
          <div className="flex gap-2">
            <button
              onClick={fetchLeaderboard}
              className="bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white px-5 py-2 rounded-lg text-sm font-semibold transition-colors"
            >
              Refresh
            </button>
            <button
              onClick={handleRefreshStats}
              disabled={isRefreshing}
              className="bg-amber-600 hover:bg-amber-500 active:bg-amber-700 disabled:opacity-50 text-white px-5 py-2 rounded-lg text-sm font-semibold transition-colors flex items-center gap-2"
            >
              {isRefreshing ? (
                <>
                  <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                  </svg>
                  Fetching…
                </>
              ) : (
                <>&#8635; Refresh Stats</>
              )}
            </button>
            <button
              onClick={handleSync}
              disabled={isSyncing}
              className="bg-purple-700 hover:bg-purple-600 active:bg-purple-800 disabled:opacity-50 text-white px-5 py-2 rounded-lg text-sm font-semibold transition-colors flex items-center gap-2"
            >
              {isSyncing ? (
                <>
                  <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                  </svg>
                  Syncing…
                </>
              ) : (
                <>&#8635; Sync Fantasy</>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* ── Scheduler Status Bar ───────────────────────────────────────── */}
      {statusInfo && (
        <div className="flex flex-wrap gap-x-6 gap-y-1 mb-4 px-1 text-xs text-gray-500">
          <span>
            <span className="text-gray-600 mr-1">數據更新</span>
            <span className="text-gray-400">{timeAgo(statusInfo.fetchedAt)}</span>
            {statusInfo.nextStatsRun && (
              <span className="text-gray-600 ml-2">
                · 下次排程 <span className="text-gray-400">{formatNextRun(statusInfo.nextStatsRun)}</span>
              </span>
            )}
          </span>
          <span>
            <span className="text-gray-600 mr-1">名單同步</span>
            <span className="text-gray-400">{timeAgo(statusInfo.fantasyAt)}</span>
            {statusInfo.nextFantasyRun && (
              <span className="text-gray-600 ml-2">
                · 下次同步 <span className="text-gray-400">{formatNextRun(statusInfo.nextFantasyRun)}</span>
              </span>
            )}
          </span>
        </div>
      )}

      {/* ── Percentile Legend ──────────────────────────────────────────── */}
      <div className="flex items-center gap-2 mb-4 text-xs text-gray-400">
        <span className="mr-1">Percentile:</span>
        {LEGEND.map(({ label, style }) => (
          <span
            key={label}
            style={style}
            className="px-2 py-0.5 rounded text-xs font-medium"
          >
            {label}
          </span>
        ))}
      </div>

      {/* ── States ─────────────────────────────────────────────────────── */}
      {loading && (
        <div className="text-center py-16 text-gray-400 text-sm">
          <svg className="animate-spin inline-block w-5 h-5 mr-2 text-blue-400" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          Loading…
        </div>
      )}

      {error && !loading && (
        <div className="bg-red-950/50 border border-red-700 text-red-300 px-4 py-3 rounded-lg text-sm">
          Error: {error}
        </div>
      )}

      {/* ── Data Table ─────────────────────────────────────────────────── */}
      {data && !loading && (
        <>
          <p className="text-sm text-gray-400 mb-3">
            Showing{' '}
            <span className="text-white font-medium">{data.count}</span> players ·
            Min {data.min_requirement} {data.data[0]?.sample_type ?? 'PA'} ·
            Metric:{' '}
            <span className="text-blue-400 font-medium">
              {METRIC_LABELS[data.metric_name] ?? data.metric_name}
            </span>
          </p>

          <div className="overflow-x-auto rounded-xl border border-gray-700">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-800 text-gray-300 text-left text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 text-center w-10">#</th>
                  <th className="px-4 py-3">Player</th>
                  <th className="px-4 py-3 text-center">Team</th>
                  <th className="px-4 py-3 text-center">Pos</th>
                  <th className="px-4 py-3 text-right">
                    {METRIC_LABELS[data.metric_name] ?? data.metric_name}
                  </th>
                  <th className="px-4 py-3 text-center">
                    {data.data[0]?.sample_type ?? 'PA'}
                  </th>
                  <th className="px-4 py-3 text-center">Pct.</th>
                  <th className="px-4 py-3 text-center">Fantasy</th>
                </tr>
              </thead>
              <tbody>
                {data.data.map((row, idx) => (
                  <tr
                    key={row.player_id}
                    className={`border-t border-gray-800 hover:bg-gray-800/60 transition-colors ${
                      idx % 2 === 0 ? 'bg-gray-900' : 'bg-gray-900/40'
                    }`}
                  >
                    <td className="px-4 py-3 text-center text-gray-500 font-mono text-xs">
                      {row.rank}
                    </td>
                    <td className="px-4 py-3 font-semibold">
                      {row.player_name}
                      {row.percentile >= 90 && !row.is_owned && (
                        <span className="ml-2 text-xs font-bold text-yellow-400 border border-yellow-600 px-1 rounded">
                          Target
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className="bg-gray-700 text-gray-200 px-2 py-0.5 rounded text-xs font-mono">
                        {row.team}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center text-gray-400 text-xs">{row.position}</td>
                    <td className="px-4 py-3 text-right font-mono font-semibold">
                      {formatValue(data.metric_name, row.avg_value)}
                    </td>
                    <td className="px-4 py-3 text-center text-gray-400 font-mono text-xs">
                      {row.sample_size}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {/* Pill-shaped percentile badge with contrast-aware text */}
                      <span
                        style={getPercentileStyle(row.percentile)}
                        className="inline-flex items-center justify-center w-10 h-6 rounded-full text-xs font-bold"
                      >
                        {row.percentile}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center">
                      {row.is_owned ? (
                        <span className="bg-gray-700 text-gray-200 px-2 py-0.5 rounded text-xs font-mono truncate max-w-[8rem] inline-block">
                          {row.fantasy_team}
                        </span>
                      ) : (
                        <span className="bg-green-900 text-green-300 border border-green-700 px-2 py-0.5 rounded text-xs font-bold">
                          FA
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
