import { useState } from 'react'
import { deleteCamera } from '../api/registry'
import EditCameraModal from './EditCameraModal'

export default function CameraList({ cameras, onSelect, token, onCameraChange }) {
  const [editingCamera, setEditingCamera] = useState(null)
  const [deletingId, setDeletingId] = useState(null)

  async function handleDelete(e, cam) {
    e.stopPropagation()
    if (!window.confirm(`Delete camera "${cam.name}" (${cam.id})? This cannot be undone.`)) return
    setDeletingId(cam.dbId)
    try {
      await deleteCamera(cam.dbId, token)
      onCameraChange()
    } catch (err) {
      alert(`Failed to delete: ${err.message}`)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="camera-list">
      <p className="panel-section__title">CAMERAS ({cameras.length})</p>
      <div className="camera-list__scroll">
        {cameras.map((cam) => (
          <div className="camera-row" key={cam.id} onClick={() => onSelect(cam)}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 3 }}>
              <span style={{ fontWeight: 500 }}>{cam.name}</span>
              <StatusBadge status={cam.healthStatus} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div className="camera-row__id">{cam.id} · {cam.department}</div>
              <div style={{ display: 'flex', gap: 4 }}>
                <button
                  className="row-icon-btn"
                  title="Edit"
                  onClick={(e) => { e.stopPropagation(); setEditingCamera(cam) }}
                >
                  ✎
                </button>
                <button
                  className="row-icon-btn row-icon-btn--danger"
                  title="Delete"
                  disabled={deletingId === cam.dbId}
                  onClick={(e) => handleDelete(e, cam)}
                >
                  {deletingId === cam.dbId ? '…' : '🗑'}
                </button>
              </div>
            </div>
          </div>
        ))}
        {cameras.length === 0 && (
          <div style={{ padding: 16, color: 'var(--ink-soft)', fontSize: 12, textAlign: 'center' }}>
            No cameras match these filters.
          </div>
        )}
      </div>

      {editingCamera && (
        <EditCameraModal
          camera={editingCamera}
          token={token}
          onClose={() => setEditingCamera(null)}
          onSuccess={onCameraChange}
        />
      )}
    </div>
  )
}

function StatusBadge({ status }) {
  const label = status[0].toUpperCase() + status.slice(1)
  return <span className={`status-badge status-badge--${status}`}>{label}</span>
}
