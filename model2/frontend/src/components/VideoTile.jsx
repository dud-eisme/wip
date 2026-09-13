import { useState, useEffect, useRef } from 'react'
import { startSourceWorker, stopSourceWorker, buildStreamUrl, createAnprJob, getAnprJob } from '../api/viewer'

const POLL_INTERVAL_MS = 2000
const TERMINAL_STATUSES = ['completed', 'failed']

export default function VideoTile({ source, workerStatus, token, onWorkerChange }) {
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState(null) // { id, status, processed_frames, events_found }
  const pollTimerRef = useRef(null)

  const isRunning = workerStatus?.is_running ?? false
  const lastError = workerStatus?.last_error

  // Stop any in-flight poll if this tile unmounts (e.g. source list refreshes).
  useEffect(() => {
    return () => { if (pollTimerRef.current) clearTimeout(pollTimerRef.current) }
  }, [])

  function pollJob(jobId) {
    getAnprJob(token, jobId).then((updated) => {
      setJob(updated)
      if (!TERMINAL_STATUSES.includes(updated.status)) {
        pollTimerRef.current = setTimeout(() => pollJob(jobId), POLL_INTERVAL_MS)
      }
    }).catch(() => {
      // Stop polling on a fetch error rather than retrying forever.
    })
  }

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
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    setJob({ status: 'queued' })
    try {
      const newJob = await createAnprJob(token, source.id)
      setJob(newJob)
      if (!TERMINAL_STATUSES.includes(newJob.status)) {
        pollTimerRef.current = setTimeout(() => pollJob(newJob.id), POLL_INTERVAL_MS)
      }
    } catch (err) {
      setJob({ status: 'failed' })
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
        {job && (
          <span className={`job-status-chip job-status-chip--${job.status}`}>
            {job.status}
            {job.status === 'completed' && job.events_found !== undefined && ` (${job.events_found} found)`}
          </span>
        )}
      </div>
    </div>
  )
}
