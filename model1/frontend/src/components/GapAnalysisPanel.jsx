export default function GapAnalysisPanel({ gapData }) {
  if (!gapData) return null

  return (
    <div>
      <p className="panel-section__title">GAP ANALYSIS</p>
      <div className="gap-card">
        <div className="gap-stat">
          <span className="gap-stat__label">Dead zone grid cells</span>
          <span className="gap-stat__value">{gapData.uncoveredZones}</span>
        </div>
        <div className="gap-stat">
          <span className="gap-stat__label">Flagged for maintenance</span>
          <span className="gap-stat__value">{gapData.ageingInfrastructure}</span>
        </div>
      </div>

      {gapData.headline && (
        <p style={{ fontSize: 12, color: 'var(--ink-soft)', marginTop: 10, lineHeight: 1.5 }}>
          {gapData.headline}
        </p>
      )}

      {gapData.recommendedActions?.length > 0 && (
        <ul style={{ fontSize: 11, color: 'var(--ink-soft)', marginTop: 6, paddingLeft: 16 }}>
          {gapData.recommendedActions.map((action, i) => (
            <li key={i} style={{ marginBottom: 3 }}>{action}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
