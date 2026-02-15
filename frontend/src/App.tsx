import { useEffect, useMemo, useState } from 'react'

type JobState = {
    job_id: string
    status: 'queued' | 'running' | 'done' | 'error'
    progress: number
    message: string
    error?: string | null
    failure_stage?: string | null
    events?: Array<{
        stage: string
        level: 'info' | 'warning' | 'error'
        message: string
        detail?: Record<string, unknown>
    }>
    result?: {
        episode?: {
            script: string
            audio_path: string
            source_urls: string[]
            skipped: { url: string; reason: string }[]
        }
        materials?: Array<{
            kind: string
            title: string
            url: string
            tweet_text: string
            content: string
            method?: string
        }>
        skipped?: { url: string; reason: string }[]
    }
}

const API_BASE = 'http://localhost:8000'

type AuthStatus = {
    logged_in: boolean
    source?: 'db' | 'env'
    user_id?: string
    scope?: string
    updated_at?: string
}

export default function App() {
    const [count, setCount] = useState(5)
    const [jobId, setJobId] = useState<string | null>(null)
    const [job, setJob] = useState<JobState | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [auth, setAuth] = useState<AuthStatus | null>(null)

    const refreshAuthStatus = async () => {
        try {
            const resp = await fetch(`${API_BASE}/api/auth/x/status`)
            if (!resp.ok) return
            const data: AuthStatus = await resp.json()
            setAuth(data)
        } catch {
            // noop for PoC
        }
    }

    const loginWithX = () => {
        const returnTo = encodeURIComponent(window.location.origin)
        window.location.href = `${API_BASE}/api/auth/x/login?return_to=${returnTo}`
    }

    const logoutX = async () => {
        if (auth?.source === 'env') {
            setError('現在は .env のトークンでログイン状態です。backend/.env の X_ACCESS_TOKEN / X_BEARER_TOKEN を空にしてください。')
            return
        }
        await fetch(`${API_BASE}/api/auth/x/logout`, { method: 'POST' })
        await refreshAuthStatus()
    }

    const canGenerate = useMemo(() => !loading && count >= 1 && count <= 100, [loading, count])

    useEffect(() => {
        refreshAuthStatus()
    }, [])

    useEffect(() => {
        const params = new URLSearchParams(window.location.search)
        const authResult = params.get('x_auth')
        const reason = params.get('reason')
        if (!authResult) return

        if (authResult === 'success') {
            setError(null)
            refreshAuthStatus()
        } else if (authResult === 'error') {
            setError(`X auth failed: ${reason ?? 'unknown'}`)
        }

        params.delete('x_auth')
        params.delete('reason')
        const cleaned = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ''}`
        window.history.replaceState({}, '', cleaned)
    }, [])

    const createJob = async () => {
        setLoading(true)
        setError(null)
        setJob(null)
        try {
            const resp = await fetch(`${API_BASE}/api/jobs/create?count=${count}`, { method: 'POST' })
            if (!resp.ok) {
                const txt = await resp.text()
                throw new Error(txt)
            }
            const data = await resp.json()
            setJobId(data.job_id)
        } catch (e) {
            setError(String(e))
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        if (!jobId) return
        const timer = setInterval(async () => {
            const resp = await fetch(`${API_BASE}/api/jobs/${jobId}`)
            if (!resp.ok) return
            const data: JobState = await resp.json()
            setJob(data)
            if (data.status === 'done' || data.status === 'error') {
                clearInterval(timer)
            }
        }, 2000)
        return () => clearInterval(timer)
    }, [jobId])

    const episode = job?.result?.episode
    const materials = job?.result?.materials || []
    const skipped = episode?.skipped || job?.result?.skipped || []

    return (
        <>
            <h1>DigLIKE PoC</h1>

            <div className="card">
                <h2>X 認証</h2>
                <p>
                    status:{' '}
                    {auth?.logged_in ? `logged in (${auth.source ?? 'unknown'})` : 'not logged in'}
                </p>
                {auth?.source === 'env' && (
                    <p>
                        ※ .env トークンでログイン中のため、画面からはログアウトできません。
                    </p>
                )}
                {auth?.user_id && <p>user_id: {auth.user_id}</p>}
                {!auth?.logged_in ? (
                    <button onClick={loginWithX}>Xでログイン</button>
                ) : auth?.source === 'db' ? (
                    <button onClick={logoutX}>ログアウト</button>
                ) : (
                    <button onClick={logoutX}>ログアウト（.envモード）</button>
                )}
            </div>

            <div className="card">
                <h2>生成設定</h2>
                <input
                    type="number"
                    min={1}
                    max={100}
                    value={count}
                    onChange={(e) => setCount(Number(e.target.value || 5))}
                />
                <p>最近いいねした順に最大 {count} 件を取得</p>
                <button onClick={createJob} disabled={!canGenerate}>Generate</button>
            </div>

            {(jobId || job) && (
                <div className="card">
                    <h2>進捗</h2>
                    <p>job_id: {jobId}</p>
                    <p>status: {job?.status ?? 'queued'}</p>
                    <p>progress: {job?.progress ?? 0}%</p>
                    <p>message: {job?.message ?? 'starting...'}</p>
                    {job?.failure_stage && <p>failure_stage: {job.failure_stage}</p>}
                    {job?.error && <p style={{ color: 'red' }}>error: {job.error}</p>}
                </div>
            )}

            {job?.events && job.events.length > 0 && (
                <div className="card">
                    <h2>実行ログ</h2>
                    <ul>
                        {job.events.map((ev, i) => (
                            <li key={`${ev.stage}-${ev.message}-${i}`}>
                                [{ev.level}] {ev.stage}: {ev.message}
                                {ev.detail ? ` ${JSON.stringify(ev.detail)}` : ''}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {error && <div className="card" style={{ color: 'red' }}>{error}</div>}

            {episode && (
                <>
                    <div className="card">
                        <h2>抽出素材（要約なし）</h2>
                        {materials.length === 0 ? (
                            <p>抽出素材データがありません</p>
                        ) : (
                            <ul>
                                {materials.map((m, i) => (
                                    <li key={`${m.url}-${i}`}>
                                        <p><strong>{m.title}</strong> ({m.kind})</p>
                                        <p>
                                            <a href={m.url} target="_blank" rel="noreferrer">
                                                {m.url}
                                            </a>
                                        </p>
                                        {m.tweet_text && (
                                            <>
                                                <p><strong>投稿者コメント</strong></p>
                                                <pre>{m.tweet_text}</pre>
                                            </>
                                        )}
                                        <p><strong>抽出本文</strong></p>
                                        <pre>{m.content}</pre>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>

                    <div className="card">
                        <h2>Audio</h2>
                        <audio controls src={`${API_BASE}${episode.audio_path}`} style={{ width: '100%' }} />
                    </div>

                    <div className="card">
                        <h2>台本</h2>
                        <pre>{episode.script}</pre>
                    </div>

                    <div className="card">
                        <h2>元URL一覧</h2>
                        <ul>
                            {episode.source_urls.map((u) => (
                                <li key={u}>
                                    <a href={u} target="_blank" rel="noreferrer">
                                        {u}
                                    </a>
                                </li>
                            ))}
                        </ul>
                    </div>

                    <div className="card">
                        <h2>スキップ理由</h2>
                        {skipped.length === 0 ? (
                            <p>なし</p>
                        ) : (
                            <ul>
                                {skipped.map((s, i) => (
                                    <li key={`${s.url}-${i}`}>
                                        {s.url} - {s.reason}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </>
            )}
        </>
    )
}