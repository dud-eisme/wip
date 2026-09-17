// Reusable yes/no confirmation dialog. Deliberately mirrors the
// modal-backdrop / modal-panel / modal-actions classes RegisterSourceModal
// already uses, so it looks native to the app rather than a one-off popup.
export default function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  busy = false,
  error = null,
  onConfirm,
  onCancel,
}) {
  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div className="modal-panel modal-panel--small" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="modal-close" onClick={onCancel} disabled={busy}>×</button>
        </div>

        <div className="modal-form">
          <p style={{ fontSize: 13, color: 'var(--ink-soft)', margin: '4px 0 16px', lineHeight: 1.5 }}>
            {message}
          </p>

          {error && <div className="form-error">{error}</div>}

          <div className="modal-actions">
            <button type="button" className="btn" onClick={onCancel} disabled={busy}>
              {cancelLabel}
            </button>
            <button
              type="button"
              className={`btn ${danger ? 'btn--danger' : 'btn--primary'}`}
              onClick={onConfirm}
              disabled={busy}
              // Inline fallback so this reads as destructive even if a
              // .btn--danger rule hasn't been added to the stylesheet yet.
              style={danger ? { background: '#dc2626', borderColor: '#dc2626', color: '#fff' } : undefined}
            >
              {busy ? 'Working…' : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
