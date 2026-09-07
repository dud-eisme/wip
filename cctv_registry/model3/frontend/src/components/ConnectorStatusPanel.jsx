export default function ConnectorStatusPanel({ adapters }) {
  return (
    <div>
      <p className="section-title">CONNECTOR STATUS</p>
      <div className="connector-grid">
        {adapters.map((adapter) => (
          <div className="connector-card" key={adapter.id}>
            <div className="connector-card__header">
              <span className="connector-card__name">{adapter.name}</span>
              <span className={`connector-status-badge connector-status-badge--${adapter.status}`}>
                <span className="connector-status-dot" />
                {adapter.status === 'connected' ? 'Connected' : 'Error'}
              </span>
            </div>
            <div className="connector-card__source">{adapter.sourceModel}</div>
            <div className="connector-card__meta">
              <span>{adapter.recordsSynced} records synced</span>
              <span>{new Date(adapter.lastSync).toLocaleTimeString()}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
