import { useEffect, useState } from 'react'
import { Activity, ShieldCheck } from 'lucide-react'

import { SyntheticDataBadge } from './components/SyntheticDataBadge'
import { apiGet, type HealthResponse } from './lib/api'

type Probe =
  | { state: 'loading' }
  | { state: 'ok'; health: HealthResponse }
  | { state: 'error'; message: string }

/**
 * Phase 0 shell: proves the toolchain renders and that the browser can reach
 * the FastAPI backend. Routing and the member chat arrive in Phase 6.
 */
export default function App() {
  const [probe, setProbe] = useState<Probe>({ state: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    apiGet<HealthResponse>('/api/health', controller.signal)
      .then((health) => setProbe({ state: 'ok', health }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setProbe({
          state: 'error',
          message: error instanceof Error ? error.message : 'Unknown error',
        })
      })
    return () => controller.abort()
  }, [])

  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-ink-200 bg-white">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-3 px-5 py-4">
          <ShieldCheck aria-hidden="true" className="size-6 text-brand-600" />
          <span className="text-lg font-semibold tracking-tight">MemberAssist</span>
          <span className="text-sm text-ink-500">Secure insurance assistant</span>
          <span className="ml-auto">
            <SyntheticDataBadge />
          </span>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-5 py-10">
        <h1 className="text-2xl font-semibold tracking-tight">Phase 0 — scaffold</h1>
        <p className="mt-2 max-w-prose text-ink-600">
          The React app, the FastAPI backend and the task runner are in place. The member chat,
          security gateway and plan-document search are built in the phases that follow.
        </p>

        <section
          aria-labelledby="backend-status"
          className="mt-8 rounded-xl border border-ink-200 bg-white p-5"
        >
          <h2 id="backend-status" className="flex items-center gap-2 font-medium">
            <Activity aria-hidden="true" className="size-4 text-brand-600" />
            Backend connection
          </h2>
          {probe.state === 'loading' && (
            <p className="mt-3 text-sm text-ink-500">Checking /api/health…</p>
          )}
          {probe.state === 'error' && (
            <p className="mt-3 text-sm text-danger-500">
              Could not reach the backend: {probe.message}. Start it with{' '}
              <code className="rounded bg-ink-100 px-1 py-0.5">uv run python manage.py api</code>.
            </p>
          )}
          {probe.state === 'ok' && (
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-ink-500">Status</dt>
              <dd className="font-medium text-brand-700">{probe.health.status}</dd>
              <dt className="text-ink-500">Version</dt>
              <dd className="font-medium">{probe.health.version}</dd>
              <dt className="text-ink-500">Environment</dt>
              <dd className="font-medium">{probe.health.env}</dd>
            </dl>
          )}
        </section>
      </main>

      <footer className="border-t border-ink-200 px-5 py-4 text-center text-xs text-ink-400">
        Student project for the CBIT AI Training programme · all member data is synthetic
      </footer>
    </div>
  )
}
