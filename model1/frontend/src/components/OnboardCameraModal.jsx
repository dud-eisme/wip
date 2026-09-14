import { useState } from 'react'
import { addCamera } from '../api/registry'

const DEPARTMENTS = ['Police', 'Transport', 'Municipal', 'Admin']
const CAMERA_TYPES = ['PTZ', 'Fixed Bullet', 'Dome', 'ANPR']
const OWNERSHIPS = ['Government', 'Private', 'PPP']
const CONNECTIVITY_STATUSES = ['Online', 'Offline', 'Degraded']
const HEALTH_STATUSES = ['Operational', 'Maintenance Required', 'Defective']
const STORAGE_TYPES = ['local', 'cloud', 'hybrid']

const initialForm = {
  camera_identifier: '', camera_name: '', latitude: '', longitude: '',
  department: 'Police', camera_type: 'PTZ', ownership: 'Government',
  connectivity_status: 'Online', health_status: 'Operational',
  installation_date: '', stream_endpoint: '',
  retention_days: 30, storage_type: 'cloud', capacity_tb: 1,
}

export default function OnboardCameraModal({ token, onClose, onSuccess }) {
  const [form, setForm] = useState(initialForm)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  async function handleSubmit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)

    const payload = {
      camera_identifier: form.camera_identifier,
      camera_name: form.camera_name,
      latitude: parseFloat(form.latitude),
      longitude: parseFloat(form.longitude),
      department: form.department,
      camera_type: form.camera_type,
      ownership: form.ownership,
      connectivity_status: form.connectivity_status,
      health_status: form.health_status,
      installation_date: form.installation_date,
      stream_endpoint: form.stream_endpoint || null,
      storage_details: {
        retention_days: parseInt(form.retention_days, 10),
        storage_type: form.storage_type,
        capacity_tb: parseFloat(form.capacity_tb),
      },
    }

    try {
      await addCamera(payload, token)
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
          <h2>Onboard Camera</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <form onSubmit={handleSubmit} className="modal-form">
          <div className="form-row">
            <label>Camera ID</label>
            <input value={form.camera_identifier} onChange={update('camera_identifier')} required />
          </div>

          <div className="form-row">
            <label>Camera Name</label>
            <input value={form.camera_name} onChange={update('camera_name')} required />
          </div>

          <div className="form-row-split">
            <div className="form-row">
              <label>Latitude</label>
              <input type="number" step="any" value={form.latitude} onChange={update('latitude')} required />
            </div>
            <div className="form-row">
              <label>Longitude</label>
              <input type="number" step="any" value={form.longitude} onChange={update('longitude')} required />
            </div>
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

          <div className="form-row-split">
            <div className="form-row">
              <label>Health Status</label>
              <select value={form.health_status} onChange={update('health_status')}>
                {HEALTH_STATUSES.map((h) => <option key={h} value={h}>{h}</option>)}
              </select>
            </div>
            <div className="form-row">
              <label>Installation Date</label>
              <input type="date" value={form.installation_date} onChange={update('installation_date')} required />
            </div>
          </div>

          <div className="form-row">
            <label>Stream Endpoint (optional)</label>
            <input value={form.stream_endpoint} onChange={update('stream_endpoint')} placeholder="rtsp://..." />
          </div>

          <p className="panel-section__title" style={{ marginTop: 8 }}>STORAGE</p>

          <div className="form-row-split">
            <div className="form-row">
              <label>Storage Type</label>
              <select value={form.storage_type} onChange={update('storage_type')}>
                {STORAGE_TYPES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="form-row">
              <label>Retention (days)</label>
              <input type="number" value={form.retention_days} onChange={update('retention_days')} required />
            </div>
            <div className="form-row">
              <label>Capacity (TB)</label>
              <input type="number" step="any" value={form.capacity_tb} onChange={update('capacity_tb')} required />
            </div>
          </div>

          {error && <div className="form-error">{error}</div>}

          <div className="modal-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? 'Adding…' : 'Add Camera'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
