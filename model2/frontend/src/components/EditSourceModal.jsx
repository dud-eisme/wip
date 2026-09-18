import { useState } from 'react'
import { updateSource } from '../api/viewer'

// Only source_name, source_url, webrtc_url, and is_active are editable —
// matches schemas.CameraSourceUpdate on the backend exactly. camera_id and
// source_type are intentionally NOT here: re-pointing a source at a
// different camera or changing its ingest protocol isn't something the
// update endpoint supports, so those stay read-only for context and the
// user registers a new source instead if they need to change either.
export default function EditSourceModal({ token, source, onClose, onSuccess }) {
  const [sourceName, setSourceName] = useState(source.source_name)
  const [sourceUrl, setSourceUrl] = useState(source.source_url)
  const [webrtcUrl, setWebrtcUrl] = useState(source.webrtc_url || '')
  const [isActive, setIsActive] = useState(source.is_active)
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const updated = await updateSource(token, source.id, {
        sourceName,
        sourceUrl,
        webrtcUrl: webrtcUrl.trim() || null,
        isActive,
      })
      onSuccess(updated)
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
          <h2>Edit Source</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <form onSubmit={handleSubmit} className="modal-form">
          <div className="form-row">
            <label>Camera</label>
            <input value={source.camera_id} disabled style={{ color: 'var(--ink-soft)', background: 'var(--surface-sunken)' }} />
            <span style={{ fontSize: 11, color: 'var(--ink-soft)', lineHeight: 1.4, marginTop: 2 }}>
              Not editable here — register a new source to point at a different camera.
            </span>
          </div>

          <div className="form-row">
            <label>Source Type</label>
            <input value={source.source_type} disabled style={{ color: 'var(--ink-soft)', background: 'var(--surface-sunken)', textTransform: 'uppercase', fontSize: 12 }} />
            <span style={{ fontSize: 11, color: 'var(--ink-soft)', lineHeight: 1.4, marginTop: 2 }}>
              Not editable here — register a new source to change the ingest protocol.
            </span>
          </div>

          <div className="form-row">
            <label>Source Name</label>
            <input value={sourceName} onChange={(e) => setSourceName(e.target.value)} required />
          </div>

          <div className="form-row">
            <label>Source URL (used for the relay and ANPR)</label>
            <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} required />
          </div>

          <div className="form-row">
            <label>WebRTC URL (optional — WHEP endpoint for low-latency preview)</label>
            <input
              value={webrtcUrl}
              onChange={(e) => setWebrtcUrl(e.target.value)}
              placeholder="http://<host>:8889/stream/<id>/whep"
            />
          </div>

          <div className="form-row">
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
                style={{ width: 'auto' }}
              />
              Active
            </label>
            <span style={{ fontSize: 11, color: 'var(--ink-soft)', lineHeight: 1.4, marginTop: 2 }}>
              Inactive sources stay registered but are excluded from normal operation.
            </span>
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
