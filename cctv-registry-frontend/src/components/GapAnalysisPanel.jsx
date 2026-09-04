export default function GapAnalysisPanel({ gapData }) {
  if (!gapData) return null

  return (
    <div>
      <p className="panel-section__title">GAP ANALYSIS</p>
      <div className="gap-card">
        <div className="gap-stat">
          <span className="gap-stat__label">Offline / uncovered</span>
          <span className="gap-stat__value">{gapData.uncoveredZones}</span>
        </div>
        <div className="gap-stat">
          <span className="gap-stat__label">Flagged for maintenance</span>
          <span className="gap-stat__value">{gapData.ageingInfrastructure}</span>
        </div>
      </div>
    </div>
  )
}
