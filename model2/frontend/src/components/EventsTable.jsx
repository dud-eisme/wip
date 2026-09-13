import { useState } from 'react'
import { flagEvent } from '../api/viewer'

export default function EventsTable({ events, token, onEventChange, cameraNameLookup }) {
  const [noteDraft, setNoteDraft] = useState({})

  async function handleToggleFlag(event) {
    const nextFlagged = !event.is_flagged
    const note = nextFlagged ? (noteDraft[event.id] || null) : null
    const updated = await flagEvent(token, event.id, nextFlagged, note)
    onEventChange(updated)
  }

  return (
    <div className="events-panel">
      <div className="events-panel__header">
        <h2>ANPR EVENTS ({events.length})</h2>
      </div>

      <table className="events-table">
        <thead>
          <tr>
            <th>Plate</th>
            <th>Camera</th>
            <th>Detected</th>
            <th>Confidence</th>
            <th>Flag</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id}>
              <td><span className="plate-chip">{event.plate_text}</span></td>
              <td style={{ fontSize: 12 }}>{cameraNameLookup[event.camera_id] || event.camera_id}</td>
              <td style={{ color: 'var(--ink-soft)', fontSize: 12 }}>
                {new Date(event.detected_at).toLocaleString()}
              </td>
              <td>{Math.round(event.confidence * 100)}%</td>
              <td>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <button
                    className={`tag-toggle ${event.is_flagged ? 'tag-toggle--tagged' : 'tag-toggle--untagged'}`}
                    onClick={() => handleToggleFlag(event)}
                    title={event.is_flagged ? 'Remove flag' : 'Flag as vehicle of interest'}
                  >
                    {event.is_flagged ? '★' : '☆'}
                  </button>
                  {!event.is_flagged && (
                    <input
                      placeholder="note (optional)"
                      value={noteDraft[event.id] || ''}
                      onChange={(e) => setNoteDraft({ ...noteDraft, [event.id]: e.target.value })}
                      style={{ fontSize: 11, padding: '2px 6px', width: 110, border: '1px solid var(--border)', borderRadius: 4 }}
                    />
                  )}
                  {event.is_flagged && event.flagged_note && (
                    <span style={{ fontSize: 11, color: 'var(--ink-soft)' }}>{event.flagged_note}</span>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {events.length === 0 && (
        <div style={{ padding: 20, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 12 }}>
          No events match this search.
        </div>
      )}
    </div>
  )
}
