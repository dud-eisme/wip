import { useState, useEffect, useRef } from 'react'
import {
  startSourceWorker,
  stopSourceWorker,
  buildStreamUrl,
  startContinuousAnprJob,
  stopAnprJob,
  getActiveAnprJob,
  getAnprJob,
  deleteSource,
} from '../api/viewer'
import ConfirmDialog from './ConfirmDialog'
import EditSourceModal from './EditSourceModal'
import WebRTCPlayer from './WebRTCPlayer'

const POLL_INTERVAL_MS = 2000
// Statuses that mean the continuous job has ended on its own (source ran
// out, or the backend's max_frames safety cap kicked in) — not statuses
// the user's toggle produces directly, since turning it off just clears
// local state without waiting on one more poll.
const TERMINAL_STATUSES = ['completed', 'failed']

export default function VideoTile({ source, workerStatus, token, onWorkerChange, onDelete, onSourceUpdate }) {
  const [busy, setBusy] = useState(false)
  const [anprBusy, setAnprBusy] = useState(false)
  const [job, setJob] = useState(null) // { id, status, processed_frames, events_found } while the overlay is live
  const pollTimerRef = useRef(null)

  // 'mjpeg'  = server relay. Higher latency, but ANPR boxes are burned in
  //            server-side, and it works through our auth.
  // 'webrtc' = direct low-latency playback via source.webrtc_url. No ANPR
  //            boxes (the frontend never sees detections for this path —
  //            see overlay.py's docstring, which only wires the relay/
  //            snapshot paths), and it bypasses our token auth entirely
  //            since it's played directly off whatever the WHEP endpoint
  //            allows — only offered when the source actually has one.
  const [playbackMode, setPlaybackMode] = useState('mjpeg')

  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState(null)

  const [isEditing, setIsEditing] = useState(false)

  const isRunning = workerStatus?.is_running ?? false
  const lastError = workerStatus?.last_error
  const anprOn = job !== null && !TERMINAL_STATUSES.includes(job.status)
  const hasWebrtc = Boolean(source.webrtc_url)

  // On mount (and whenever the source/token changes), recover whether a
  // continuous ANPR job is already live for this source — e.g. after a
  // page refresh — so the toggle reflects reality instead of resetting
  // to "off" while boxes are still being drawn server-side.
  useEffect(() => {
    let cancelled = false
    getActiveAnprJob(token, source.id)
      .then((activeJob) => {
        if (cancelled || !activeJob) return
        setJob(activeJob)
        pollTimerRef.current = setTimeout(() => pollContinuousJob(activeJob.id), POLL_INTERVAL_MS)
      })
      .catch(() => {
        // No active job / fetch failed — stay off, nothing to recover.
      })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id, token])

  // Stop any in-flight poll if this tile unmounts (e.g. source list refreshes,
  // or this tile is the one just deleted).
  useEffect(() => {
    return () => { if (pollTimerRef.current) clearTimeout(pollTimerRef.current) }
  }, [])

  // If the WebRTC playback mode is active but a source edit just removed
  // the webrtc_url, fall back to the relay view rather than leaving the
  // tile pointed at a dead <WebRTCPlayer url={undefined}>.
  useEffect(() => {
    if (!hasWebrtc && playbackMode === 'webrtc') setPlaybackMode('mjpeg')
  }, [hasWebrtc, playbackMode])

  function pollContinuousJob(jobId) {
    getAnprJob(token, jobId)
      .then((updated) => {
        if (TERMINAL_STATUSES.includes(updated.status)) {
          // Job ended on its own rather than via the toggle (source
          // ended, or hit the safety cap) — flip the toggle back off.
          setJob(null)
          return
        }
        setJob(updated)
        pollTimerRef.current = setTimeout(() => pollContinuousJob(jobId), POLL_INTERVAL_MS)
      })
      .catch(() => {
        // Stop polling on a fetch error rather than retrying forever.
        setJob(null)
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

  async function handleToggleAnpr() {
    if (anprOn) {
      // --- turn overlay OFF ---
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
      setAnprBusy(true)
      try {
        if (job?.id) await stopAnprJob(token, job.id)
      } catch (err) {
        // Even if the stop request itself failed, drop the toggle back to
        // off locally — the alternative is a button stuck saying "on" with
        // no way to retry the stop.
      } finally {
        setJob(null)
        setAnprBusy(false)
      }
      return
    }

    // --- turn overlay ON ---
    setAnprBusy(true)
    try {
      const newJob = await startContinuousAnprJob(token, source.id)
      setJob(newJob)
      pollTimerRef.current = setTimeout(() => pollContinuousJob(newJob.id), POLL_INTERVAL_MS)
    } catch (err) {
      setJob(null)
    } finally {
      setAnprBusy(false)
    }
  }

  async function handleConfirmDelete() {
    setDeleting(true)
    setDeleteError(null)
    try {
      // Backend stops this source's capture worker itself as part of the
      // delete (see routers/sources.py delete_source), so no need to call
      // stopSourceWorker here first.
      await deleteSource(token, source.id)
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
      onDelete(source.id)
      // Deliberately no setDeleting(false)/setConfirmingDelete(false) on
      // success — this tile is about to be unmounted by the parent
      // removing it from the sources list, so resetting local state here
      // would just be a wasted render (or a set-state-after-unmount
      // warning if the parent re-renders slower than this callback).
    } catch (err) {
      setDeleteError(err.message)
      setDeleting(false)
    }
  }

  const showWebrtc = playbackMode === 'webrtc' && hasWebrtc
  const showMjpeg = !showWebrtc && isRunning

  return (
    <div className="video-tile-wrapper">
      <div className={`video-tile ${!showWebrtc && !isRunning ? 'video-tile__offline' : ''}`}>
        {showWebrtc ? (
          <WebRTCPlayer url={source.webrtc_url} label={source.source_name} />
        ) : showMjpeg ? (
          // Token passed as a query param here specifically because a plain
          // <img> tag cannot send an Authorization header — see the
          // buildStreamUrl comment in api/viewer.js. Detection boxes (while
          // anprOn) are burned into these frames server-side, so nothing
          // extra needs to be drawn on the frontend.
          <img className="video-tile__feed" src={buildStreamUrl(source.id, token)} alt={source.source_name} />
        ) : (
          <div className="video-tile__placeholder">
            {lastError ? `Error: ${lastError}` : 'Worker not started'}
          </div>
        )}

        <div className="video-tile__label">
          <span>
            <span className={`video-tile__status-dot video-tile__status-dot--${showWebrtc || isRunning ? 'online' : 'offline'}`} />
            {source.source_name}
          </span>
          <span style={{ opacity: 0.7, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            {showWebrtc ? 'webrtc' : source.source_type}
          </span>
        </div>
      </div>

      <div className="video-tile__controls">
        {isRunning ? (
          <button className="btn btn--small" onClick={handleStop} disabled={busy}>Stop</button>
        ) : (
          <button className="btn btn--small btn--primary" onClick={handleStart} disabled={busy}>Start</button>
        )}

        {hasWebrtc && (
          <button
            className="btn btn--small"
            onClick={() => setPlaybackMode((m) => (m === 'webrtc' ? 'mjpeg' : 'webrtc'))}
            title={
              showWebrtc
                ? 'Switch back to the server relay (higher latency, shows ANPR boxes)'
                : 'Switch to direct WebRTC playback (low latency, no ANPR boxes)'
            }
          >
            {showWebrtc ? 'Relay view' : 'Low latency'}
          </button>
        )}

        <button
          className={`btn btn--small ${anprOn ? 'btn--primary' : ''}`}
          onClick={handleToggleAnpr}
          disabled={anprBusy}
        >
          {anprOn ? 'Stop ANPR' : 'Start ANPR'}
        </button>

        {job && (
          <span className={`job-status-chip job-status-chip--${anprOn ? 'running' : job.status}`}>
            {anprOn
              ? `ANPR live${job.events_found !== undefined ? ` · ${job.events_found} found` : ''}`
              : job.status}
          </span>
        )}

        <button
          className="btn btn--small"
          onClick={() => setIsEditing(true)}
          disabled={busy || anprBusy}
          title="Edit this source"
          style={{ marginLeft: 'auto' }}
        >
          Edit
        </button>

        <button
          className="btn btn--small"
          onClick={() => setConfirmingDelete(true)}
          disabled={busy || anprBusy}
          title="Delete this source"
          style={{ color: '#dc2626', borderColor: '#dc2626' }}
        >
          Delete
        </button>
      </div>

      {isEditing && (
        <EditSourceModal
          token={token}
          source={source}
          onClose={() => setIsEditing(false)}
          onSuccess={(updatedSource) => onSourceUpdate(updatedSource)}
        />
      )}

      {confirmingDelete && (
        <ConfirmDialog
          title="Delete source?"
          message={
            isRunning || anprOn
              ? `"${source.source_name}" is currently live${anprOn ? ' with ANPR running' : ''}. Deleting it will stop the feed and permanently remove this source. This can't be undone.`
              : `Permanently remove "${source.source_name}"? This can't be undone.`
          }
          confirmLabel="Delete"
          danger
          busy={deleting}
          error={deleteError}
          onConfirm={handleConfirmDelete}
          onCancel={() => {
            setConfirmingDelete(false)
            setDeleteError(null)
          }}
        />
      )}
    </div>
  )
}
