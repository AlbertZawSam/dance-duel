import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError, fmtTime, type Attempt, type Challenge, type Preflight, type PracticePose } from '../api'
import { ErrorBox, Spinner, StickFigure } from '../components'

type Phase = 'loading' | 'setup' | 'preview' | 'countdown' | 'performing' | 'uploading' | 'analyzing' | 'error'

const COUNTDOWN_S = 3

function pickMimeType(): string {
  const options = ['video/webm;codecs=vp8', 'video/webm', 'video/mp4']
  return options.find((t) => typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(t)) ?? ''
}

/**
 * Official attempt: the song plays, the reference stays on the server, and the screen
 * shows only neutral controls, an optional self-preview and elapsed time.
 * Practice: the same capture flow, plus the reference stick figure when permitted.
 */
export default function Play() {
  const { slug = '' } = useParams()
  const [params] = useSearchParams()
  const mode = params.get('mode') === 'official' ? 'official' : 'practice'
  const duelId = params.get('duel') ?? undefined
  const navigate = useNavigate()

  const [phase, setPhase] = useState<Phase>('loading')
  const [error, setError] = useState<string | null>(null)
  const [challenge, setChallenge] = useState<Challenge | null>(null)
  const [pose, setPose] = useState<PracticePose | null>(null)
  const [check, setCheck] = useState<Preflight | null>(null)
  const [checkNote, setCheckNote] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [showPreview, setShowPreview] = useState(true)
  const [mirrorPreview, setMirrorPreview] = useState(true)
  const [count, setCount] = useState(COUNTDOWN_S)
  const [elapsed, setElapsed] = useState(0)
  const [progress, setProgress] = useState(0)

  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioCtx = useRef<AudioContext | null>(null)
  const buffer = useRef<AudioBuffer | null>(null)
  const source = useRef<AudioBufferSourceNode | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const songStartCtx = useRef(0) // AudioContext time when the song starts
  const attemptRef = useRef<Attempt | null>(null)
  const cancelled = useRef(false)

  const version = challenge?.current_version ?? null
  const duration = version?.duration_s ?? 0

  // Song time in seconds, from the audio clock (not wall-clock timers).
  const songTime = useCallback(() => {
    const ctx = audioCtx.current
    return ctx ? ctx.currentTime - songStartCtx.current : 0
  }, [])

  const stopAudio = () => {
    try {
      source.current?.stop()
    } catch {
      /* already stopped */
    }
    source.current = null
  }

  // Load challenge, song and (practice only) the reference skeleton; open the camera.
  useEffect(() => {
    cancelled.current = false
    ;(async () => {
      try {
        const ch = await api.challenge(slug)
        if (!ch.current_version) throw new Error('This challenge has no published version.')
        setChallenge(ch)
        const ctx = new AudioContext()
        audioCtx.current = ctx
        const blob = await api.mediaBlob(`/challenge-versions/${ch.current_version.id}/audio`)
        buffer.current = await ctx.decodeAudioData(await blob.arrayBuffer())
        if (mode === 'practice' && ch.current_version.has_practice_reference) {
          setPose(await api.practicePose(ch.current_version.id))
        }
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 960 }, height: { ideal: 540 }, frameRate: { ideal: 30 } },
          audio: false,
        })
        if (cancelled.current) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        streamRef.current = stream
        setPhase('setup')
      } catch (e) {
        const msg =
          e instanceof DOMException && e.name === 'NotAllowedError'
            ? 'Camera permission was denied. Allow camera access to play.'
            : (e as Error).message
        setError(msg)
        setPhase('error')
      }
    })()
    return () => {
      cancelled.current = true
      stopAudio()
      if (recorder.current?.state === 'recording') recorder.current.stop()
      streamRef.current?.getTracks().forEach((t) => t.stop())
      audioCtx.current?.close()
      const a = attemptRef.current
      if (a && a.status === 'started') api.abortAttempt(a.id).catch(() => {})
    }
  }, [slug, mode])

  // Keep the <video> attached to the stream whenever it is (re)rendered.
  useEffect(() => {
    if (videoRef.current && streamRef.current && videoRef.current.srcObject !== streamRef.current) {
      videoRef.current.srcObject = streamRef.current
    }
  })

  // Elapsed-time display while performing.
  useEffect(() => {
    if (phase !== 'performing' && phase !== 'preview') return
    const id = setInterval(() => setElapsed(Math.max(0, songTime())), 200)
    return () => clearInterval(id)
  }, [phase, songTime])

  const runCameraCheck = async () => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return
    setChecking(true)
    setCheckNote(null)
    const canvas = document.createElement('canvas')
    canvas.width = v.videoWidth
    canvas.height = v.videoHeight
    canvas.getContext('2d')!.drawImage(v, 0, 0)
    const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, 'image/jpeg', 0.85))
    try {
      setCheck(await api.preflight(blob!))
    } catch (e) {
      setCheck(null)
      setCheckNote(
        e instanceof ApiError && e.status === 503
          ? 'The camera check service is unavailable. You can still play; the server rejects recordings it cannot see clearly.'
          : (e as Error).message,
      )
    } finally {
      setChecking(false)
    }
  }

  const playSong = (startDelay: number, onEnded: () => void) => {
    const ctx = audioCtx.current!
    const src = ctx.createBufferSource()
    src.buffer = buffer.current
    src.connect(ctx.destination)
    songStartCtx.current = ctx.currentTime + startDelay
    src.onended = onEnded
    src.start(songStartCtx.current)
    source.current = src
  }

  const startPreview = async () => {
    await audioCtx.current?.resume()
    setPhase('preview')
    playSong(0.2, () => setPhase((p) => (p === 'preview' ? 'setup' : p)))
  }

  const stopPreview = () => {
    stopAudio()
    setPhase('setup')
  }

  const start = async () => {
    if (!version || !streamRef.current) return
    setError(null)
    const ctx = audioCtx.current!
    await ctx.resume()
    let attempt: Attempt
    try {
      attempt = await api.createAttempt(version.id, mode, duelId)
    } catch (e) {
      setError((e as Error).message)
      return
    }
    attemptRef.current = attempt

    const mimeType = pickMimeType()
    const rec = new MediaRecorder(streamRef.current, mimeType ? { mimeType, videoBitsPerSecond: 2_500_000 } : undefined)
    const chunks: Blob[] = []
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data)
    recorder.current = rec

    const recStart = await new Promise<number>((resolve) => {
      rec.onstart = () => resolve(performance.now())
      rec.start(1000)
    })

    setPhase('countdown')
    setCount(COUNTDOWN_S)
    // Schedule the song on the audio clock right after the countdown.
    playSong(COUNTDOWN_S, () => finish())
    for (let i = COUNTDOWN_S; i > 0; i--) {
      setCount(i)
      await new Promise((r) => setTimeout(r, 1000))
      if (cancelled.current || recorder.current !== rec) return
    }
    setPhase('performing')

    // Map the song start from the audio clock onto the recording's timeline, including
    // the measured output latency (when the sound actually leaves the speakers).
    const ts = ctx.getOutputTimestamp()
    const perfAtSongStart =
      (ts.performanceTime ?? performance.now()) + (songStartCtx.current - (ts.contextTime ?? ctx.currentTime)) * 1000
    const outputLatencyMs = ((ctx as AudioContext & { outputLatency?: number }).outputLatency ?? ctx.baseLatency ?? 0) * 1000
    const songOffsetMs = perfAtSongStart + outputLatencyMs - recStart

    async function finish() {
      if (recorder.current !== rec || rec.state !== 'recording') return
      await new Promise((r) => setTimeout(r, 400)) // keep a little tail after the last beat
      const stopped = new Promise<void>((r) => (rec.onstop = () => r()))
      rec.stop()
      await stopped
      const recordingMs = performance.now() - recStart
      recorder.current = null
      const blob = new Blob(chunks, { type: (rec.mimeType || 'video/webm').split(';')[0] })
      await upload(attempt.id, blob, songOffsetMs, recordingMs)
    }
  }

  const upload = async (attemptId: string, blob: Blob, offsetMs: number, recordingMs: number) => {
    setPhase('uploading')
    setProgress(0)
    for (let tries = 0; ; tries++) {
      try {
        const a = await api.uploadRecording(attemptId, blob, offsetMs, recordingMs, setProgress)
        attemptRef.current = a
        break
      } catch (e) {
        // Retrying with the same attempt id is safe: the server keeps the first upload.
        if (tries >= 2 || (e instanceof ApiError && e.status >= 400 && e.status < 500)) {
          setError((e as Error).message)
          setPhase('error')
          return
        }
        await new Promise((r) => setTimeout(r, 1500 * (tries + 1)))
      }
    }
    setPhase('analyzing')
    while (!cancelled.current) {
      const a = await api.attempt(attemptId).catch(() => null)
      if (a && !['queued', 'processing'].includes(a.status)) {
        navigate(`/attempts/${a.id}`, { replace: true })
        return
      }
      await new Promise((r) => setTimeout(r, 1500))
    }
  }

  const abort = async () => {
    const rec = recorder.current
    recorder.current = null
    stopAudio()
    if (rec?.state === 'recording') rec.stop()
    const a = attemptRef.current
    if (a) await api.abortAttempt(a.id).catch(() => {})
    attemptRef.current = null
    setPhase('setup')
  }

  if (phase === 'loading') return <Spinner label="Loading challenge, song and camera…" />
  if (phase === 'error')
    return (
      <div className="stack narrow">
        <ErrorBox error={error} />
        <Link to={duelId ? `/duels/${duelId}` : `/challenges/${slug}`}>Back</Link>
      </div>
    )

  const live = phase === 'countdown' || phase === 'performing'
  const officialLive = mode === 'official' && live

  return (
    <div className="play">
      <header className="play-head">
        <div>
          <h1>{challenge?.title}</h1>
          <span className={`pill ${mode === 'official' ? 'pill-hot' : ''}`}>
            {mode === 'official' ? 'Official attempt · song only' : 'Practice · unranked'}
          </span>{' '}
          <span className="muted small">
            v{version?.version} · {fmtTime(duration)}
          </span>
        </div>
      </header>

      <div className={`stage ${pose && !officialLive ? 'with-guide' : ''}`}>
        <div className="camera">
          {(showPreview || !live) && (
            <video ref={videoRef} autoPlay playsInline muted className={mirrorPreview ? 'mirrored' : ''} />
          )}
          {!showPreview && live && <div className="camera-off">Self-preview hidden</div>}
          {phase === 'countdown' && <div className="countdown">{count}</div>}
          {live && phase === 'performing' && (
            <div className="hud">
              {fmtTime(elapsed)} / {fmtTime(duration)}
            </div>
          )}
        </div>
        {pose && !officialLive && (
          <div className="guide">
            <StickFigure pose={pose} getTime={phase === 'preview' || phase === 'performing' ? songTime : () => 0} />
            <p className="muted small">Reference (practice only)</p>
          </div>
        )}
      </div>

      {phase === 'setup' && (
        <div className="stack">
          <section className="card">
            <h3>1. Camera check</h3>
            <p className="muted">
              Stand back so your whole body, head to feet, is in frame. Use a clear, safe space and normal room lighting.
            </p>
            <div className="row">
              <button onClick={runCameraCheck} disabled={checking}>
                {checking ? 'Checking…' : 'Check my camera'}
              </button>
              {check && (
                <ul className="checks">
                  <li className={check.checks.one_person ? 'ok' : 'bad'}>
                    {check.checks.one_person ? 'One person' : `${check.people} people detected`}
                  </li>
                  <li className={check.checks.full_body ? 'ok' : 'bad'}>Full body {check.checks.full_body ? 'visible' : 'not visible'}</li>
                  <li className={check.checks.lighting ? 'ok' : 'bad'}>Lighting {check.checks.lighting ? 'OK' : 'too dark'}</li>
                </ul>
              )}
            </div>
            {checkNote && <p className="muted small">{checkNote}</p>}
          </section>

          <section className="card">
            <h3>2. Orientation</h3>
            <p className="muted">
              {version?.mirror_policy === 'mirror_player'
                ? 'This challenge is danced mirrored: when the reference dancer raises the arm on your screen-left, you raise your right arm.'
                : "Dance the same body sides as the reference dancer: the dancer's left arm is your left arm."}{' '}
              The mirrored self-preview is only a display preference and never changes scoring.
            </p>
            <label className="check">
              <input type="checkbox" checked={mirrorPreview} onChange={(e) => setMirrorPreview(e.target.checked)} /> Mirror my
              preview
            </label>
            <label className="check">
              <input type="checkbox" checked={showPreview} onChange={(e) => setShowPreview(e.target.checked)} /> Show my
              preview while dancing
            </label>
          </section>

          <section className="card">
            <h3>3. {mode === 'official' ? 'Official attempt' : 'Practice run'}</h3>
            {mode === 'official' ? (
              <p className="muted">
                Only the song plays. The reference dancer stays hidden. The recording is uploaded for analysis, and the raw
                video is deleted as soon as it is scored (at most 24 hours). Your score is revealed when both players
                finish.
              </p>
            ) : (
              <p className="muted">
                Practice scores never count towards duels or ratings. Recordings are uploaded for analysis and deleted
                after scoring.
              </p>
            )}
            <ErrorBox error={error} />
            <div className="row">
              {pose && (
                <button className="secondary" onClick={startPreview}>
                  Watch the routine
                </button>
              )}
              <button className={mode === 'official' ? 'hot' : ''} onClick={start}>
                {mode === 'official' ? 'Lock in and start' : 'Start practice run'}
              </button>
              {!check?.ok && <span className="muted small">Tip: run the camera check first.</span>}
            </div>
          </section>
        </div>
      )}

      {phase === 'preview' && (
        <div className="row center">
          <span className="muted">
            {fmtTime(elapsed)} / {fmtTime(duration)}
          </span>
          <button className="secondary" onClick={stopPreview}>
            Stop
          </button>
        </div>
      )}

      {live && (
        <div className="row center">
          <button className="secondary" onClick={() => setShowPreview((s) => !s)}>
            {showPreview ? 'Hide' : 'Show'} self-preview
          </button>
          <button className="danger" onClick={abort}>
            Stop &amp; abort
          </button>
        </div>
      )}

      {phase === 'uploading' && (
        <div className="stack narrow">
          <p>Uploading your recording…</p>
          <div className="bar-track">
            <span className="bar-fill" style={{ width: `${progress * 100}%` }} />
          </div>
        </div>
      )}
      {phase === 'analyzing' && <Spinner label="Analyzing your movement against the hidden reference…" />}
    </div>
  )
}
