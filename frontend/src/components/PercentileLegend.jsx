import { LEGEND } from '../utils/format'

export default function PercentileLegend() {
  return (
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
  )
}
