import { useState } from 'react'
import { startSourceWorker, stopSourceWorker, buildStreamUrl, createAnprJob } from '../api/viewer'

export default function VideoTile({ source, workerStatus, token, onWorkerChange }) {
  const [busy, setBusy] = useState(false)
  const [jobStatus, setJobStatus] = useState(null)

  const isRunning = workerStatus?.is_running ?? false
  const lastError = workerStatus?.last_error

  async function handleStart() {
    setBusy(true)
    try {
      const status = await startSourceWorker(token, source.id)
      onWorkerChange(source.id, status)
    } catch (err) {
      onWorkerChange(source.id, { source_id: source.id, is_running: false, last_error: err.message })
    } finally {
      setBusy(false)
    }
  }

  async function handleStop() {
    setBusy(true)
    try {
      await stopSourceWorker(token, source.id)
      onWorkerChange(source.id, { source_id: source.id, is_running: false })
    } finally {
      setBusy(false)
    }
  }

  async function handleRunAnpr() {
    setJobStatus('queued')
    try {
      const job = await createAnprJob(token, source.id)
      setJobStatus(job.status)
    } catch (err) {
      setJobStatus('failed')
    }
  }

  return (
    <div className="video-tile-wrapper">
      <div className={`video-tile ${!isRunning ? 'video-tile__offline' : ''}`}>
        {isRunning ? (
          // Token passed as a query param here specifically because a plain
          // <img> tag cannot send an Authorization header — see the
          // buildStreamUrl comment in api/viewer.js.
          <img className="video-tile__feed" src={buildStreamUrl(source.id, token)} alt={source.source_name} />
        ) : (
          <div className="video-tile__placeholder">
            {lastError ? `Error: ${lastError}` : 'Worker not started'}
          </div>
        )}

        <div className="video-tile__label">
          <span>
            <span className={`video-tile__status-dot video-tile__status-dot--${isRunning ? 'online' : 'offline'}`} />
            {source.source_name}
          </span>
          <span style={{ opacity: 0.7, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            {source.source_type}
          </span>
        </div>
      </div>

      <div className="video-tile__controls">
        {isRunning ? (
          <button className="btn btn--small" onClick={handleStop} disabled={busy}>Stop</button>
        ) : (
          <button className="btn btn--small btn--primary" onClick={handleStart} disabled={busy}>Start</button>
        )}
        <button className="btn btn--small" onClick={handleRunAnpr} disabled={busy}>
          Run ANPR
        </button>
        {jobStatus && <span className="job-status-chip">{jobStatus}</span>}
      </div>
    </div>
  )
}
