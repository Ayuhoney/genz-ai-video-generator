const TOKEN_KEY = 'ai-video-platform.token'
const USER_KEY = 'ai-video-platform.auth'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ??
  'http://localhost:8000'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setAuthSession(token: string, user: unknown): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearAuthSession(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function readStoredUser<T>(): T | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    if (!raw) {
      return null
    }
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

function authHeaders(): HeadersInit {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function redirectToLoginOnUnauthorized(): void {
  clearAuthSession()
  if (window.location.pathname !== '/login' && window.location.pathname !== '/register') {
    const from = `${window.location.pathname}${window.location.search}`
    window.location.assign(`/login?from=${encodeURIComponent(from)}`)
  }
}

async function parseError(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json()
    if (
      typeof data === 'object' &&
      data !== null &&
      'detail' in data
    ) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') {
        return detail
      }
      if (Array.isArray(detail)) {
        return detail
          .map((item) => {
            if (typeof item === 'object' && item !== null && 'msg' in item) {
              return String((item as { msg: unknown }).msg)
            }
            return JSON.stringify(item)
          })
          .join(', ')
      }
    }
  } catch {
    // fall through
  }
  return response.statusText || 'Request failed'
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  auth = true,
): Promise<T> {
  const headers = new Headers(options.headers)
  if (!headers.has('Content-Type') && options.body) {
    headers.set('Content-Type', 'application/json')
  }
  if (auth) {
    const token = getToken()
    if (token) {
      headers.set('Authorization', `Bearer ${token}`)
    }
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  })

  if (response.status === 401) {
    redirectToLoginOnUnauthorized()
    throw new ApiError('Not authenticated', 401)
  }

  if (response.status === 204) {
    return undefined as T
  }

  if (!response.ok) {
    throw new ApiError(await parseError(response), response.status)
  }

  return (await response.json()) as T
}

export { API_BASE_URL, authHeaders }
