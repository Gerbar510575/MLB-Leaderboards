import { useState, useEffect, useCallback } from 'react'
import FilterBar       from './FilterBar'
import SchedulerStatus from './SchedulerStatus'
import PercentileLegend from './PercentileLegend'
import LeaderboardTable from './LeaderboardTable'

export default function Leaderboard() {
  // ── Filter state ──────────────────────────────────────────────────────
  const [availableMetrics,  setAvailableMetrics]  = useState([])
  const [availableSeasons,  setAvailableSeasons]  = useState([])
  const [metric, setMetric] = useState('exit_velocity')
  const [limit,  setLimit]  = useState(500)
  const [minReq, setMinReq] = useState(5)
  const [year,   setYear]   = useState(null)  // null = current season

  // ── Data state ────────────────────────────────────────────────────────
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)
  const [cacheHit, setCacheHit] = useState(null)  // true | false | null

  // ── Action state ──────────────────────────────────────────────────────
  const [isSyncing,    setIsSyncing]    = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [statusInfo,   setStatusInfo]   = useState(null)

  // ── Fetch scheduler/fantasy status ───────────────────────────────────
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
    fetch('/api/v1/seasons')
      .then(r => r.json())
      .then(d => setAvailableSeasons(d.seasons ?? []))
      .catch(() => {})
    fetchStatus()
  }, [fetchStatus])

  // ── Fetch leaderboard ─────────────────────────────────────────────────
  const fetchLeaderboard = useCallback(() => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({ metric_name: metric, limit, min_requirement: minReq })
    if (year !== null) params.set('year', year)
    fetch(`/api/v1/leaderboard?${params}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        setCacheHit(res.headers.get('X-Cache-Hit') === 'true')
        return res.json()
      })
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [metric, limit, minReq, year])

  // Auto-fetch whenever filter values change
  useEffect(() => { fetchLeaderboard() }, [fetchLeaderboard])

  // ── Action handlers ───────────────────────────────────────────────────
  async function handleRefreshStats() {
    setIsRefreshing(true)
    try {
      await fetch('/api/v1/data/refresh', { method: 'POST' })
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 2000))
        const status = await fetch('/api/v1/data/status').then(r => r.json())
        if (status.refresh_job?.status === 'done')  break
        if (status.refresh_job?.status === 'error') break
      }
    } catch (_) {}
    fetchLeaderboard()
    fetchStatus()
    setIsRefreshing(false)
  }

  async function handleSync() {
    setIsSyncing(true)
    try {
      await fetch('/api/v1/fantasy/sync', { method: 'POST' })
    } catch (_) {}
    fetchLeaderboard()
    fetchStatus()
    setIsSyncing(false)
  }

  // ── Render ────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-950 text-white p-6 font-sans">

      {/* Header */}
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

        <div className="flex items-center gap-3">
          {/* Historical season badge */}
          {year !== null && (
            <span className="text-xs font-semibold px-3 py-1 rounded-full border border-indigo-500 text-indigo-300 bg-indigo-900/30">
              Historical {year}
            </span>
          )}

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
      </div>

      <FilterBar
        availableMetrics={availableMetrics}
        availableSeasons={availableSeasons}
        metric={metric} limit={limit} minReq={minReq} year={year}
        isRefreshing={isRefreshing} isSyncing={isSyncing}
        onMetricChange={setMetric}
        onLimitChange={setLimit}
        onMinReqChange={setMinReq}
        onYearChange={setYear}
        onRefresh={fetchLeaderboard}
        onRefreshStats={handleRefreshStats}
        onSync={handleSync}
      />

      <SchedulerStatus statusInfo={statusInfo} />

      <PercentileLegend />

      {/* Loading spinner */}
      {loading && (
        <div className="text-center py-16 text-gray-400 text-sm">
          <svg className="animate-spin inline-block w-5 h-5 mr-2 text-blue-400" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          Loading…
        </div>
      )}

      {/* Error */}
      {error && !loading && (
        <div className="bg-red-950/50 border border-red-700 text-red-300 px-4 py-3 rounded-lg text-sm">
          Error: {error}
        </div>
      )}

      {/* Data table */}
      {data && !loading && <LeaderboardTable data={data} />}

    </div>
  )
}
