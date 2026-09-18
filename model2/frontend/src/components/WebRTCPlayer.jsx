import { useEffect, useRef, useState } from 'react'

// Pulls a user:pass@ pair out of a URL, if present, and returns both the
// credential-free URL (safe to pass to fetch()) and the matching
// "Authorization: Basic ..." header value. Returns authHeader: null when
// the URL has no embedded credentials, so callers can skip adding the
// header entirely rather than sending an empty/bogus one.
function extractEmbeddedCredentials(rawUrl) {
  try {
    const parsed = new URL(rawUrl)
    if (!parsed.username && !parsed.password) {
      return { cleanUrl: rawUrl, authHeader: null }
    }
    const username = decodeURIComponent(parsed.username)
    const password = decodeURIComponent(parsed.password)
    parsed.username = ''
    parsed.password = ''
    return {
      cleanUrl: parsed.toString(),
      authHeader: `Basic ${btoa(`${username}:${password}`)}`,
    }
  } catch {
    // Not a parseable absolute URL — let fetch() itself surface the error.
    return { cleanUrl: rawUrl, authHeader: null }
  }
}

// Minimal WHEP (WebRTC-HTTP Egress Protocol) player.
// `url` is expected to be a WHEP endpoint (source.webrtc_url) that accepts
// an SDP offer via POST and returns an SDP answer — this is the direct,
// low-latency playback path described in VideoTile.jsx, which bypasses
// our own token auth since it's played straight off whatever the WHEP
// endpoint allows.
export default function WebRTCPlayer({ url, label }) {
  const videoRef = useRef(null)
  const pcRef = useRef(null)
  const [error, setError] = useState(null)
  const [connecting, setConnecting] = useState(true)

  useEffect(() => {
    let cancelled = false
    setError(null)
    setConnecting(true)

    async function connect() {
      const pc = new RTCPeerConnection()
      pcRef.current = pc

      // We only want to receive video (and optionally audio) from the source.
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.addTransceiver('audio', { direction: 'recvonly' })

      pc.ontrack = (event) => {
        if (videoRef.current && event.streams[0]) {
          videoRef.current.srcObject = event.streams[0]
        }
      }

      pc.onconnectionstatechange = () => {
        if (cancelled) return
        if (pc.connectionState === 'connected') setConnecting(false)
        if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
          setError('WebRTC connection lost')
        }
      }

      try {
        const offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        // fetch() refuses to load a URL with embedded credentials
        // (user:pass@host) outright — "URL is an URL with embedded
        // credentials" — even though that's a perfectly normal way for a
        // WHEP endpoint to be given out. If present, strip them out of
        // the URL and send the equivalent as a Basic auth header instead,
        // which fetch() does allow.
        const { cleanUrl, authHeader } = extractEmbeddedCredentials(url)

        const headers = { 'Content-Type': 'application/sdp' }
        if (authHeader) headers['Authorization'] = authHeader

        const res = await fetch(cleanUrl, {
          method: 'POST',
          headers,
          body: offer.sdp,
        })

        if (!res.ok) {
          throw new Error(`WHEP endpoint returned ${res.status}`)
        }

        const answerSdp = await res.text()
        if (cancelled) return
        await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp })
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to connect')
      }
    }

    connect()

    return () => {
      cancelled = true
      if (pcRef.current) {
        pcRef.current.close()
        pcRef.current = null
      }
    }
  }, [url])

  if (error) {
    return (
      <div className="video-tile__placeholder">
        {`WebRTC error: ${error}`}
      </div>
    )
  }

  return (
    <>
      <video
        ref={videoRef}
        className="video-tile__feed"
        autoPlay
        playsInline
        muted
        aria-label={label}
      />
      {connecting && (
        <div className="video-tile__placeholder" style={{ position: 'absolute', inset: 0 }}>
          Connecting…
        </div>
      )}
    </>
  )
}
