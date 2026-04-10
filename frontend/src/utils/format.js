// ---------------------------------------------------------------------------
// Metric display metadata
// ---------------------------------------------------------------------------
export const METRIC_LABELS = {
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
export const FORMAT_CONFIG = {
  exit_velocity:   { suffix: ' mph',   decimals: 1 },
  xba:             { decimals: 3, stripLeadingZero: true },  // 0.312 → .312
  xslg:            { decimals: 3, stripLeadingZero: true },
  xwoba:           { decimals: 3, stripLeadingZero: true },
  xwoba_diff:      { decimals: 3, stripLeadingZero: true, showSign: true },
  hard_hit_rate:   { suffix: '%',      decimals: 1 },
  barrel_rate:     { suffix: '%',      decimals: 1 },
  launch_angle:    { suffix: '°',      decimals: 1 },
  sprint_speed:    { suffix: ' ft/s',  decimals: 1 },
  // Pitcher metrics
  p_xera:          { decimals: 2 },
  p_era_diff:      { decimals: 2, showSign: true },
  p_xwoba_against: { decimals: 3, stripLeadingZero: true },
  p_hard_hit_rate: { suffix: '%',      decimals: 1 },
  p_barrel_rate:   { suffix: '%',      decimals: 1 },
  p_avg_ev:        { suffix: ' mph',   decimals: 1 },
  p_k9:            { decimals: 2 },
  p_bb9:           { decimals: 2 },
  p_k_bb_diff:     { suffix: '%',      decimals: 2, showSign: true },
}

export function formatValue(metric, value) {
  const cfg = FORMAT_CONFIG[metric] ?? { decimals: 2 }
  let str = value.toFixed(cfg.decimals)
  if (cfg.stripLeadingZero) str = str.replace(/^(-?)0\./, '$1.')  // -0.050 → -.050
  if (cfg.showSign && value > 0) str = '+' + str
  return str + (cfg.suffix ?? '')
}

// ---------------------------------------------------------------------------
// Percentile colour — Savant "Red Hot" convention: red = elite, blue = low
// ---------------------------------------------------------------------------
export function getPercentileStyle(pct) {
  if (pct >= 90) return { backgroundColor: '#d22d2d', color: 'white' }   // deep red  — elite
  if (pct >= 70) return { backgroundColor: '#e06c6c', color: 'white' }   // light red — above avg
  if (pct >= 40) return { backgroundColor: '#e8e8e8', color: '#1f2937' } // light gray — avg
  if (pct >= 20) return { backgroundColor: '#6ba5d9', color: 'white' }   // light blue — below avg
  return               { backgroundColor: '#1a4fa0', color: 'white' }    // deep blue  — low
}

export const LEGEND = [
  { label: '90–100', style: { backgroundColor: '#d22d2d', color: 'white' } },
  { label: '70–89',  style: { backgroundColor: '#e06c6c', color: 'white' } },
  { label: '40–69',  style: { backgroundColor: '#e8e8e8', color: '#1f2937' } },
  { label: '20–39',  style: { backgroundColor: '#6ba5d9', color: 'white' } },
  { label: '0–19',   style: { backgroundColor: '#1a4fa0', color: 'white' } },
]
