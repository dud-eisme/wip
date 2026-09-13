import { useState, useEffect } from 'react'
import { getRegistryCameras, createSource } from '../api/viewer'

const SOURCE_TYPES = ['rtsp', 'http', 'file']

export default function RegisterSourceModal({ token, onClose, onSuccess }) {
  const [cameras, setCameras] = useState([])
  const [cameraId, setCameraId] = useState('')
  const [sourceName, setSourceName] = useState('')
  const [sourceType, setSourceType] = useState('file')
  const [sourceUrl, setSourceUrl] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    getRegistryCameras(token).then((cams) => {
      setCameras(cams)
      if (cams.length > 0) setCameraId(cams[0].id)
    })
  }, [token])

  async function handleSubmit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await createSource(token, { cameraId, sourceName, sourceType, sourceUrl })
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
          <h2>Register Source</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <form onSubmit={handleSubmit} className="modal-form">
          <div className="form-row">
            <label>Camera (from Model 1 registry)</label>
            <select value={cameraId} onChange={(e) => setCameraId(e.target.value)} required>
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>{c.camera_name} ({c.camera_identifier})</option>
              ))}
            </select>
          </div>

          <div className="form-row">
            <label>Source Name</label>
            <input value={sourceName} onChange={(e) => setSourceName(e.target.value)} required />
          </div>

          <div className="form-row">
            <label>Source Type</label>
            <select value={sourceType} onChange={(e) => setSourceType(e.target.value)}>
              {SOURCE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          <div className="form-row">
            <label>Source URL</label>
            <input
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder={sourceType === 'file' ? '/path/to/clip.mp4' : `${sourceType}://...`}
              required
            />
          </div>

          {error && <div className="form-error">{error}</div>}

          <div className="modal-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? 'Registering…' : 'Register Source'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
