export default function FederatedEventsTable({ events }) {
  return (
    <div className="events-panel">
      <div className="events-panel__header">
        <h2>CORRELATED EVENTS ({events.length})</h2>
      </div>

      {/* Everything that can grow past the panel's height lives in this
         wrapper, not directly in .events-panel — see federation.css:
         .events-panel__body is the ONE element with overflow-y: auto,
         so the header above stays fixed in place while only the table
         rows scroll beneath it. */}
      <div className="events-panel__body">
        <table className="events-table">
          <thead>
            <tr>
              <th>Plate</th>
              <th>Camera</th>
              <th>Department</th>
              <th>Camera Health</th>
              <th>Reliability</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id}>
                <td><span className="plate-chip">{event.plateNumber}</span></td>
                <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{event.cameraId}</td>
                <td>{event.department}</td>
                <td style={{ textTransform: 'capitalize' }}>{event.cameraHealthStatus}</td>
                <td>
                  <span className={`reliability-badge reliability-badge--${event.reliability}`}>
                    {event.reliability}
                  </span>
                </td>
                <td style={{ color: 'var(--ink-soft)', fontSize: 12 }}>
                  {new Date(event.timestamp).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {events.length === 0 && (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 12 }}>
            No correlated events match this search.
          </div>
        )}
      </div>
    </div>
  )
}
