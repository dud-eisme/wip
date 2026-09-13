import { useState } from 'react'
import { updateCamera } from '../api/registry'

const DEPARTMENTS = ['Police', 'Transport', 'Municipal', 'Admin']
const CAMERA_TYPES = ['PTZ', 'Fixed Bullet', 'Dome', 'ANPR']
const OWNERSHIPS = ['Government', 'Private', 'PPP']
const CONNECTIVITY_STATUSES = ['Online', 'Offline', 'Degraded']
const HEALTH_STATUSES = ['Operational', 'Maintenance Required', 'Defective']

// Reverse-maps a normalized frontend camera object back into backend-shape
// values for the form's initial state (the form works in backend vocabulary
// throughout, same as OnboardCameraModal, so PATCH payloads need no
// translation at submit time).
function toBackendDefaults(camera) {
  const deptMap = { 'Police': 'Police', 'Transport Department': 'Transport', 'Municipal Corporation': 'Municipal', 'Institution': 'Admin' }
  const typeMap = { 'PTZ': 'PTZ', 'Bullet': 'Fixed Bullet', 'Fixed Dome': 'Dome', 'ANPR-enabled': 'ANPR' }
  const healthMap = { 'active': 'Operational', 'maintenance': 'Maintenance Required', 'inactive': 'Defective' }
  const connMap = { 'online': 'Online', 'offline': 'Offline', 'degraded': 'Degraded' }
  return {
    camera_name: camera.name,
    department: deptMap[camera.department] || camera.department,
    camera_type: typeMap[camera.cameraType] || camera.cameraType,
    ownership: camera.ownership,
    connectivity_status: connMap[camera.connectivityStatus] || 'Online',
    health_status: healthMap[camera.healthStatus] || 'Operational',
    stream_endpoint: camera.streamEndpoint || '',
  }
}

export default function EditCameraModal({ camera, token, onClose, onSuccess }) {
  const [form, setForm] = useState(toBackendDefaults(camera))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  async function handleSubmit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      // PATCH accepts partial updates - send only editable fields, matching
      // the backend's CameraUpdate schema.
      await updateCamera(camera.dbId, {
        camera_name: form.camera_name,
        department: form.department,
        camera_type: form.camera_type,
        ownership: form.ownership,
        connectivity_status: form.connectivity_status,
        health_status: form.health_status,
        stream_endpoint: form.stream_endpoint || null,
      }, token)
      onSuccess()
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Edit Camera — {camera.id}</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <form onSubmit={handleSubmit} className="modal-form">
          <div className="form-row">
            <label>Camera Name</label>
            <input value={form.camera_name} onChange={update('camera_name')} required />
          </div>

          <div className="form-row-split">
            <div className="form-row">
              <label>Department</label>
              <select value={form.department} onChange={update('department')}>
                {DEPARTMENTS.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
            <div className="form-row">
              <label>Camera Type</label>
              <select value={form.camera_type} onChange={update('camera_type')}>
                {CAMERA_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
          </div>

          <div className="form-row-split">
            <div className="form-row">
              <label>Ownership</label>
              <select value={form.ownership} onChange={update('ownership')}>
                {OWNERSHIPS.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
            <div className="form-row">
              <label>Connectivity</label>
              <select value={form.connectivity_status} onChange={update('connectivity_status')}>
                {CONNECTIVITY_STATUSES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>

          <div className="form-row">
            <label>Health Status</label>
            <select value={form.health_status} onChange={update('health_status')}>
              {HEALTH_STATUSES.map((h) => <option key={h} value={h}>{h}</option>)}
            </select>
          </div>

          <div className="form-row">
            <label>Stream Endpoint</label>
            <input value={form.stream_endpoint} onChange={update('stream_endpoint')} placeholder="rtsp://..." />
          </div>

          {error && <div className="form-error">{error}</div>}

          <div className="modal-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
