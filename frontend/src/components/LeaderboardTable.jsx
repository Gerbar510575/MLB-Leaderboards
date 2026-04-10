import { METRIC_LABELS, formatValue, getPercentileStyle } from '../utils/format'

// data: the full API response object
//   { metric_name, count, min_requirement, data: [...rows] }
export default function LeaderboardTable({ data }) {
  const sampleType = data.data[0]?.sample_type ?? 'PA'
  const metricLabel = METRIC_LABELS[data.metric_name] ?? data.metric_name

  return (
    <>
      <p className="text-sm text-gray-400 mb-3">
        Showing{' '}
        <span className="text-white font-medium">{data.count}</span> players ·
        Min {data.min_requirement} {sampleType} ·
        Metric: <span className="text-blue-400 font-medium">{metricLabel}</span>
      </p>

      <div className="overflow-x-auto rounded-xl border border-gray-700">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-800 text-gray-300 text-left text-xs uppercase tracking-wider">
              <th className="px-4 py-3 text-center w-10">#</th>
              <th className="px-4 py-3">Player</th>
              <th className="px-4 py-3 text-center">Team</th>
              <th className="px-4 py-3 text-center">Pos</th>
              <th className="px-4 py-3 text-right">{metricLabel}</th>
              <th className="px-4 py-3 text-center">{sampleType}</th>
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
                {/* Rank */}
                <td className="px-4 py-3 text-center text-gray-500 font-mono text-xs">
                  {row.rank}
                </td>

                {/* Player name + Target badge */}
                <td className="px-4 py-3 font-semibold">
                  {row.player_name}
                  {row.percentile >= 90 && !row.is_owned && (
                    <span className="ml-2 text-xs font-bold text-yellow-400 border border-yellow-600 px-1 rounded">
                      Target
                    </span>
                  )}
                </td>

                {/* Team */}
                <td className="px-4 py-3 text-center">
                  <span className="bg-gray-700 text-gray-200 px-2 py-0.5 rounded text-xs font-mono">
                    {row.team}
                  </span>
                </td>

                {/* Position */}
                <td className="px-4 py-3 text-center text-gray-400 text-xs">
                  {row.position}
                </td>

                {/* Value */}
                <td className="px-4 py-3 text-right font-mono font-semibold">
                  {formatValue(data.metric_name, row.avg_value)}
                </td>

                {/* Sample size */}
                <td className="px-4 py-3 text-center text-gray-400 font-mono text-xs">
                  {row.sample_size}
                </td>

                {/* Percentile pill */}
                <td className="px-4 py-3 text-center">
                  <span
                    style={getPercentileStyle(row.percentile)}
                    className="inline-flex items-center justify-center w-10 h-6 rounded-full text-xs font-bold"
                  >
                    {row.percentile}
                  </span>
                </td>

                {/* Fantasy ownership */}
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
  )
}
