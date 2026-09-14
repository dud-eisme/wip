export default function AnalyticsSummary({ summary }) {
  if (!summary) return null

  return (
    <div>
      <p className="section-title">FEDERATED ANALYTICS</p>
      <div className="analytics-summary">
        <p className="analytics-summary__headline">{summary.headline}</p>
        <div className="analytics-stats">
          <div>
            <div className="analytics-stat__value">{summary.totalEvents}</div>
            <div className="analytics-stat__label">Total federated events</div>
          </div>
          <div>
            <div className="analytics-stat__value" style={{ color: 'var(--status-offline)' }}>
              {summary.lowReliabilityCount}
            </div>
            <div className="analytics-stat__label">Low-reliability (camera issues)</div>
          </div>
          {Object.entries(summary.departmentBreakdown || {}).map(([dept, count]) => (
            <div key={dept}>
              <div className="analytics-stat__value">{count}</div>
              <div className="analytics-stat__label">{dept}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
