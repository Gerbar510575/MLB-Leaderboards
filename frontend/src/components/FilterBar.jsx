import { METRIC_LABELS } from '../utils/format'

function Spinner() {
  return (
    <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

export default function FilterBar({
  availableMetrics,
  availableSeasons,
  metric, limit, minReq, year,
  isRefreshing, isSyncing,
  onMetricChange, onLimitChange, onMinReqChange, onYearChange,
  onRefresh, onRefreshStats, onSync,
}) {
  return (
    <div className="flex flex-wrap gap-4 mb-5 bg-gray-900 p-4 rounded-xl border border-gray-700">

      {/* Metric selector */}
      <div className="flex flex-col gap-1">
        <label className="text-xs text-gray-400 uppercase tracking-wider">Metric</label>
        <select
          value={metric}
          onChange={e => onMetricChange(e.target.value)}
          className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
        >
          {availableMetrics.map(m => (
            <option key={m} value={m}>{METRIC_LABELS[m] ?? m}</option>
          ))}
        </select>
      </div>

      {/* Season selector */}
      {availableSeasons.length > 0 && (
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-400 uppercase tracking-wider">Season</label>
          <select
            value={year ?? ''}
            onChange={e => onYearChange(e.target.value ? Number(e.target.value) : null)}
            className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
          >
            <option value=''>Current</option>
            {availableSeasons.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      )}

      {/* Limit */}
      <div className="flex flex-col gap-1">
        <label className="text-xs text-gray-400 uppercase tracking-wider">Show Top</label>
        <input
          type="number" min={1} max={100} value={limit}
          onChange={e => onLimitChange(Number(e.target.value))}
          className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm w-20 focus:outline-none focus:border-blue-500"
        />
      </div>

      {/* Min PA */}
      <div className="flex flex-col gap-1">
        <label className="text-xs text-gray-400 uppercase tracking-wider">Min PA</label>
        <input
          type="number" min={1} value={minReq}
          onChange={e => onMinReqChange(Number(e.target.value))}
          className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm w-20 focus:outline-none focus:border-blue-500"
        />
      </div>

      {/* Action buttons */}
      <div className="flex flex-col justify-end gap-2 ml-auto">
        <div className="flex gap-2">

          {/* Refresh (cache re-fetch) */}
          <button
            onClick={onRefresh}
            className="bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white px-5 py-2 rounded-lg text-sm font-semibold transition-colors"
          >
            Refresh
          </button>

          {/* Refresh Stats (full pybaseball fetch) */}
          <button
            onClick={onRefreshStats}
            disabled={isRefreshing}
            className="bg-amber-600 hover:bg-amber-500 active:bg-amber-700 disabled:opacity-50 text-white px-5 py-2 rounded-lg text-sm font-semibold transition-colors flex items-center gap-2"
          >
            {isRefreshing ? <><Spinner /> Fetching…</> : <>&#8635; Refresh Stats</>}
          </button>

          {/* Sync Fantasy */}
          <button
            onClick={onSync}
            disabled={isSyncing}
            className="bg-purple-700 hover:bg-purple-600 active:bg-purple-800 disabled:opacity-50 text-white px-5 py-2 rounded-lg text-sm font-semibold transition-colors flex items-center gap-2"
          >
            {isSyncing ? <><Spinner /> Syncing…</> : <>&#8635; Sync Fantasy</>}
          </button>

        </div>
      </div>

    </div>
  )
}
