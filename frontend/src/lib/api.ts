/**
 * Thin fetch wrapper for the MemberAssist API.
 *
 * Requests always send credentials because authentication uses an httpOnly
 * cookie — the token is never readable from JavaScript.
 */

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: 'GET',
    credentials: 'include',
    headers: { Accept: 'application/json' },
    ...(signal ? { signal } : {}),
  })
  if (!response.ok) {
    throw new ApiError(response.status, `GET ${path} failed with ${response.status}`)
  }
  return (await response.json()) as T
}

export interface HealthResponse {
  status: string
  app: string
  version: string
  env: string
  uptime_seconds: number
  synthetic_data: boolean
}
