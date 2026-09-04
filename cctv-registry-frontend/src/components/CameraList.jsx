export default function CameraList({ cameras }) {
  return (
    <div className="camera-list">
      <p className="panel-section__title">CAMERAS ({cameras.length})</p>
      <div className="camera-list__scroll">
        {cameras.map((cam) => (
          <div className="camera-row" key={cam.id}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 3 }}>
              <span style={{ fontWeight: 500 }}>{cam.name}</span>
              <StatusBadge status={cam.healthStatus} />
            </div>
            <div className="camera-row__id">{cam.id} · {cam.department}</div>
          </div>
        ))}
        {cameras.length === 0 && (
          <div style={{ padding: 16, color: 'var(--ink-soft)', fontSize: 12, textAlign: 'center' }}>
            No cameras match these filters.
          </div>
        )}
      </div>
    </div>
  )
}

function StatusBadge({ status }) {
  const label = status[0].toUpperCase() + status.slice(1)
  return <span className={`status-badge status-badge--${status}`}>{label}</span>
}
