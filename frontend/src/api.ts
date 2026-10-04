export type User = { id: number; username: string; rating: number }

export type ChallengeVersion = {
  id: number
  version: number
  duration_s: number
  mirror_policy: 'none' | 'mirror_player'
  has_practice_video: boolean
  has_practice_reference: boolean
  pose_model: string
  license_note: string
  published_at: string
}

export type Challenge = {
  id: number
  slug: string
  title: string
  description: string
  difficulty: string
  current_version: ChallengeVersion | null
}

export type FeedbackNote = { kind: 'improve' | 'strength'; start_s: number; end_s: number; text: string; tip: string | null }
export type EventResult = { name: string; time_s: number; offset_ms: number | null; score: number }
export type Coverage = { full_body_ratio: number; min_window_coverage: number }

export type ScoreBreakdown = {
  total: number
  pose: number
  timing: number
  dynamics: number
  flow: number
  analysis_version: string
  coverage?: Coverage
  events?: EventResult[]
  timeline?: { start_s: number; end_s: number; pose: number }[]
  feedback?: FeedbackNote[]
}

export type AttemptStatus = 'started' | 'queued' | 'processing' | 'scored' | 'invalid' | 'technical_failure' | 'aborted'

export type Attempt = {
  id: string
  mode: 'practice' | 'official'
  status: AttemptStatus
  challenge_version_id: number
  challenge_slug: string
  duel_id: string | null
  created_at: string
  result_hidden: boolean
  result: ScoreBreakdown | null
  invalid_reason?: string
  invalid_detail?: string
  duration_s?: number
}

export type DuelSide = ScoreBreakdown & { attempt_id: string; rating_delta: number }

export type Duel = {
  id: string
  status: 'invited' | 'awaiting_attempts' | 'analyzing' | 'finalized' | 'declined' | 'expired'
  challenge: { id: number; slug: string; title: string }
  challenge_version: number
  challenge_version_id: number
  challenger: User
  opponent: User
  role: 'challenger' | 'opponent'
  created_at: string
  expires_at: string
  my_progress: string
  opponent_progress: string
  attempts_left: number
  outcome: null | {
    winner: string | null
    is_draw: boolean
    challenger: DuelSide
    opponent: DuelSide
    finalized_at: string
  }
}

export type Weekly = {
  timezone: string
  week_start: string
  week_end: string
  min_duels_for_best_users: number
  trending_challenges: { challenge_id: number; slug: string; title: string; duels: number; players: number }[]
  best_users: { user_id: number; username: string; rating: number; weekly_duels: number }[]
}

export type UserRecord = {
  username: string
  rating: number
  battles: number
  wins: number
  losses: number
  draws: number
  win_rate: number | null
  best_score: number | null
  average_score: number | null
  average_components: Record<string, number>
  history: {
    duel_id: string
    finalized_at: string
    challenge: string
    opponent: string
    my_score: number
    opponent_score: number
    result: 'win' | 'loss' | 'draw'
    rating_delta: number
  }[]
}

export type Preflight = {
  ok: boolean
  checks: { one_person: boolean; full_body: boolean; lighting: boolean }
  people: number
  brightness: number
}

export type PracticePose = { fps: number; aspect: number; frames: [number, number, number][][] }

const TOKEN_KEY = 'dd_token'

export const tokenStore = {
  get: () => {
    try {
      return localStorage.getItem(TOKEN_KEY)
    } catch {
      return null
    }
  },
  set: (t: string | null) => {
    try {
      if (t) localStorage.setItem(TOKEN_KEY, t)
      else localStorage.removeItem(TOKEN_KEY)
    } catch {
      /* storage unavailable: session lasts until reload */
    }
  },
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function authHeaders(): Record<string, string> {
  const t = tokenStore.get()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { ...authHeaders(), ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : res.statusText
    if (res.status === 401) window.dispatchEvent(new Event('dd:unauthorized'))
    throw new ApiError(res.status, detail)
  }
  return data as T
}

export const api = {
  register: (username: string, password: string) =>
    request<{ token: string; user: User }>('POST', '/auth/register', { username, password }),
  login: (username: string, password: string) =>
    request<{ token: string; user: User }>('POST', '/auth/login', { username, password }),
  logout: () => request<void>('POST', '/auth/logout'),
  me: () => request<User>('GET', '/me'),
  searchUsers: (q: string) => request<User[]>('GET', `/users?q=${encodeURIComponent(q)}`),
  record: (username: string) => request<UserRecord>('GET', `/users/${encodeURIComponent(username)}/record`),

  challenges: () => request<Challenge[]>('GET', '/challenges'),
  challenge: (slug: string) => request<Challenge>('GET', `/challenges/${encodeURIComponent(slug)}`),
  practicePose: (versionId: number) => request<PracticePose>('GET', `/challenge-versions/${versionId}/practice-pose`),

  createAttempt: (challenge_version_id: number, mode: 'practice' | 'official', duel_id?: string) =>
    request<Attempt>('POST', '/attempts', { challenge_version_id, mode, duel_id: duel_id ?? null }),
  attempt: (id: string) => request<Attempt>('GET', `/attempts/${id}`),
  attempts: (mode?: string) => request<Attempt[]>('GET', `/attempts${mode ? `?mode=${mode}` : ''}`),
  abortAttempt: (id: string) => request<Attempt>('POST', `/attempts/${id}/abort`),

  duels: () => request<Duel[]>('GET', '/duels'),
  duel: (id: string) => request<Duel>('GET', `/duels/${id}`),
  createDuel: (opponent_username: string, challenge_slug: string) =>
    request<Duel>('POST', '/duels', { opponent_username, challenge_slug }),
  acceptDuel: (id: string) => request<Duel>('POST', `/duels/${id}/accept`),
  declineDuel: (id: string) => request<Duel>('POST', `/duels/${id}/decline`),

  weekly: () => request<Weekly>('GET', '/dashboard/weekly'),

  /** Protected media is fetched with the auth header, then played from an object URL. */
  async mediaBlob(path: string): Promise<Blob> {
    const res = await fetch(`/api${path}`, { headers: authHeaders() })
    if (!res.ok) throw new ApiError(res.status, 'Could not load media.')
    return res.blob()
  },

  async preflight(image: Blob): Promise<Preflight> {
    const form = new FormData()
    form.append('image', image, 'frame.jpg')
    const res = await fetch('/api/preflight', { method: 'POST', headers: authHeaders(), body: form })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new ApiError(res.status, data.detail ?? 'Camera check failed.')
    return data
  },

  /** Upload with progress; re-sending to the same attempt id is idempotent on the server. */
  uploadRecording(
    attemptId: string,
    video: Blob,
    songOffsetMs: number,
    recordingDurationMs: number,
    onProgress: (fraction: number) => void,
  ): Promise<Attempt> {
    return new Promise((resolve, reject) => {
      const form = new FormData()
      const ext = video.type.includes('mp4') ? 'mp4' : 'webm'
      form.append('video', video, `attempt.${ext}`)
      form.append('song_offset_ms', String(Math.max(0, Math.round(songOffsetMs))))
      form.append('recording_duration_ms', String(Math.round(recordingDurationMs)))
      const xhr = new XMLHttpRequest()
      xhr.open('PUT', `/api/attempts/${attemptId}/recording`)
      const t = tokenStore.get()
      if (t) xhr.setRequestHeader('Authorization', `Bearer ${t}`)
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
      xhr.onload = () => {
        let data: { detail?: string } & Partial<Attempt> = {}
        try {
          data = JSON.parse(xhr.responseText)
        } catch {
          /* ignore */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data as Attempt)
        else reject(new ApiError(xhr.status, data.detail ?? 'Upload failed.'))
      }
      xhr.onerror = () => reject(new ApiError(0, 'Network error during upload.'))
      xhr.send(form)
    })
  },
}

export const fmtTime = (s: number) => {
  const t = Math.max(0, Math.round(s))
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`
}

export const fmtScore = (v: number | null | undefined) => (v == null ? '–' : v.toFixed(1))
