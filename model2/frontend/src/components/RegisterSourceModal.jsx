import { useState, useEffect } from 'react'
import { getRegistryCameras, createSource } from '../api/viewer'

const SOURCE_TYPES = ['rtsp', 'http', 'file']

const SOURCE_TYPE_HINTS = {
  rtsp: 'rtsp://<host>:8554/stream/<id>',
  http: 'http://<host>/...',
  file: '/path/to/clip.mp4',
}

const SOURCE_TYPE_NOTES = {
  rtsp: 'Lowest-latency ingestion path',
  http: 'Plain HTTP stream (e.g. MJPEG).',
  file: 'Local recorded clip — useful for testing ANPR without a live feed.',
}

export default function RegisterSourceModal({ token, onClose, onSuccess }) {
  const [cameras, setCameras] = useState([])
  const [cameraId, setCameraId] = useState('')
  const [sourceName, setSourceName] = useState('')
  const [sourceType, setSourceType] = useState(SOURCE_TYPES[0])
  const [sourceUrl, setSourceUrl] = useState('')
  const [webrtcUrl, setWebrtcUrl] = useState('')
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
      await createSource(token, {
        cameraId,
        sourceName,
        sourceType,
        sourceUrl,
        // Optional — only server-relayed/ANPR'd sources can meaningfully
        // offer a low-latency WebRTC preview, since OpenCV/ffmpeg can't
        // ingest WHEP itself (see models.py's webrtc_url comment). Blank
        // is sent through as null so createSource/the backend treat it as
        // "no WebRTC preview available for this source".
        webrtcUrl: webrtcUrl.trim() || null,
      })
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
            <span style={{ fontSize: 11, color: 'var(--ink-soft)', lineHeight: 1.4, marginTop: 2 }}>
              {SOURCE_TYPE_NOTES[sourceType]}
            </span>
          </div>

          <div className="form-row">
            <label>Source URL (used for the relay and ANPR)</label>
            <input
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder={SOURCE_TYPE_HINTS[sourceType]}
              required
            />
          </div>

          <div className="form-row">
            <label>WebRTC URL (optional — WHEP endpoint for low-latency preview)</label>
            <input
              value={webrtcUrl}
              onChange={(e) => setWebrtcUrl(e.target.value)}
              placeholder="http://<host>:8889/stream/<id>/whep"
            />
            <span style={{ fontSize: 11, color: 'var(--ink-soft)', lineHeight: 1.4, marginTop: 2 }}>
              Leave blank if this source has no WHEP endpoint. The relay and ANPR always use
              the Source URL above — this only powers the optional "Low latency" browser
              preview toggle.
            </span>
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
