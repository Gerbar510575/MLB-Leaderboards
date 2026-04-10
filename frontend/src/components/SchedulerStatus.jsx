function timeAgo(isoStr) {
  if (!isoStr) return '—'
  const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000)
  if (diff < 60)    return `${diff} 秒前`
  if (diff < 3600)  return `${Math.floor(diff / 60)} 分鐘前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`
  return `${Math.floor(diff / 86400)} 天前`
}

function formatNextRun(isoStr) {
  if (!isoStr) return '—'
  return new Date(isoStr).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

// statusInfo: { fetchedAt, nextStatsRun, fantasyAt, nextFantasyRun }
export default function SchedulerStatus({ statusInfo }) {
  if (!statusInfo) return null

  return (
    <div className="flex flex-wrap gap-x-6 gap-y-1 mb-4 px-1 text-xs text-gray-500">
      <span>
        <span className="text-gray-600 mr-1">數據更新</span>
        <span className="text-gray-400">{timeAgo(statusInfo.fetchedAt)}</span>
        {statusInfo.nextStatsRun && (
          <span className="text-gray-600 ml-2">
            · 下次排程{' '}
            <span className="text-gray-400">{formatNextRun(statusInfo.nextStatsRun)}</span>
          </span>
        )}
      </span>
      <span>
        <span className="text-gray-600 mr-1">名單同步</span>
        <span className="text-gray-400">{timeAgo(statusInfo.fantasyAt)}</span>
        {statusInfo.nextFantasyRun && (
          <span className="text-gray-600 ml-2">
            · 下次同步{' '}
            <span className="text-gray-400">{formatNextRun(statusInfo.nextFantasyRun)}</span>
          </span>
        )}
      </span>
    </div>
  )
}
