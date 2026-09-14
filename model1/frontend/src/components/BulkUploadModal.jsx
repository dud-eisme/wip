import { useState } from 'react'
import { bulkImportCameras } from '../api/registry'

export default function BulkUploadModal({ token, onClose, onSuccess }) {
  const [file, setFile] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function handleUpload() {
    if (!file) return
    setSubmitting(true)
    setError(null)
    setResult(null)

    try {
      const data = await bulkImportCameras(file, token)
      setResult(data)
      if (data.inserted_count > 0) {
        onSuccess()
      }
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
          <h2>Bulk Import Cameras</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-form">
          <p style={{ fontSize: 12, color: 'var(--ink-soft)' }}>
            Upload a CSV or Excel file. Required columns: camera_identifier, camera_name,
            latitude, longitude, department, camera_type, ownership, connectivity_status,
            retention_days, storage_type, capacity_tb, health_status, installation_date.
          </p>

          <input
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={(e) => setFile(e.target.files[0])}
          />

          {error && <div className="form-error">{error}</div>}

          {result && (
            <div className="bulk-result">
              <div className="gap-stat">
                <span className="gap-stat__label">Total rows</span>
                <span className="gap-stat__value">{result.total_rows}</span>
              </div>
              <div className="gap-stat">
                <span className="gap-stat__label">Inserted</span>
                <span className="gap-stat__value" style={{ color: 'var(--status-active)' }}>
                  {result.inserted_count}
                </span>
              </div>
              <div className="gap-stat">
                <span className="gap-stat__label">Failed</span>
                <span className="gap-stat__value" style={{ color: 'var(--status-offline)' }}>
                  {result.failed_count}
                </span>
              </div>

              {result.errors.length > 0 && (
                <div className="bulk-errors">
                  {result.errors.map((err, i) => (
                    <div key={i} className="bulk-error-row">
                      <strong>Row {err.row_number}:</strong> {err.errors.join('; ')}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="modal-actions">
            <button type="button" className="btn" onClick={onClose}>
              {result ? 'Close' : 'Cancel'}
            </button>
            {!result && (
              <button
                type="button"
                className="btn btn--primary"
                onClick={handleUpload}
                disabled={!file || submitting}
              >
                {submitting ? 'Uploading…' : 'Upload'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
