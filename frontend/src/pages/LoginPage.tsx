import { useMemo, useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Button } from '../components/Button'
import { Input } from '../components/FormFields'
import { useAuthContext } from '../hooks/useAuthContext'

interface FormErrors {
  name?: string
  email?: string
  password?: string
}

type AuthMode = 'login' | 'register'

export function LoginPage({ initialMode = 'login' }: { initialMode?: AuthMode }) {
  const { login, register, isAuthenticated, isLoading, error } = useAuthContext()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const fromQuery = searchParams.get('from')
  const from =
    fromQuery ||
    (location.state as { from?: string } | null)?.from ||
    '/dashboard'

  const [mode, setMode] = useState<AuthMode>(initialMode)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<FormErrors>({})
  const [submitting, setSubmitting] = useState(false)

  const canSubmit = useMemo(() => {
    if (submitting) {
      return false
    }
    if (mode === 'register' && !name.trim()) {
      return false
    }
    return email.trim().length > 0 && password.length > 0
  }, [email, mode, name, password, submitting])

  if (!isLoading && isAuthenticated) {
    return <Navigate to={from} replace />
  }

  const validate = (): boolean => {
    const next: FormErrors = {}
    if (mode === 'register' && !name.trim()) {
      next.name = 'Name is required.'
    }
    if (!email.trim()) {
      next.email = 'Email is required.'
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      next.email = 'Enter a valid email address.'
    }
    if (!password) {
      next.password = 'Password is required.'
    } else if (password.length < 6) {
      next.password = 'Password must be at least 6 characters.'
    }
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!validate()) {
      return
    }
    setSubmitting(true)
    try {
      if (mode === 'register') {
        await register(email, password, name.trim())
      } else {
        await login(email, password)
      }
      navigate(from, { replace: true })
    } catch {
      // error shown from auth context
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="app-shell relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-12">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[var(--color-accent)]/40 to-transparent" />

      <div className="relative grid w-full max-w-5xl gap-10 lg:grid-cols-[1.05fr_0.95fr] lg:items-center">
        <div className="anim-rise hidden text-left lg:block">
          <p className="gc-eyebrow">The cinematic talent network</p>
          <h1 className="mt-4 font-sans text-4xl font-bold leading-[1.08] tracking-[-0.03em] text-[var(--color-ink)] xl:text-5xl">
            GENZ CINE
            <span className="mt-2 block text-[var(--color-accent)]">Cine AI</span>
          </h1>
          <p className="body-muted mt-5 max-w-md">
            From idea to finished film — script, scenes, voice, and render in one
            premium AI video studio.
          </p>
          <div className="mt-8 flex flex-wrap gap-x-6 gap-y-2 text-sm font-semibold uppercase tracking-[0.08em] text-[var(--color-ink-muted)]">
            <span>Global network</span>
            <span>Verified craft</span>
            <span>Instant create</span>
          </div>
        </div>

        <div className="anim-rise-delay w-full rounded-[1.35rem] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-6 shadow-[0_24px_60px_-36px_rgba(10,10,11,0.35)] sm:p-8">
          <div className="mb-6 flex flex-col items-start text-left lg:hidden">
            <p className="gc-eyebrow">Cine AI</p>
            <h1 className="mt-2 font-sans text-2xl font-bold tracking-[-0.02em] text-[var(--color-ink)]">
              GENZ CINE
            </h1>
          </div>

          <div className="mb-1">
            <h2 className="font-sans text-2xl font-bold tracking-[-0.02em] text-[var(--color-ink)]">
              {mode === 'login' ? 'Sign in' : 'Create account'}
            </h2>
            <p className="mt-1.5 text-sm leading-relaxed text-[var(--color-ink-muted)]">
              {mode === 'login'
                ? 'Sign in to continue creating cinematic AI videos.'
                : 'Register to start producing with Cine AI.'}
            </p>
          </div>

          <div className="mb-5 mt-5 grid grid-cols-2 gap-1 rounded-2xl bg-[var(--color-surface)] p-1">
            <button
              type="button"
              className={`rounded-xl px-3 py-2.5 text-sm font-semibold transition ${
                mode === 'login'
                  ? 'bg-[var(--color-surface-elevated)] text-[var(--color-ink)] shadow-sm'
                  : 'text-[var(--color-ink-muted)]'
              }`}
              onClick={() => {
                setMode('login')
                setErrors({})
              }}
            >
              Login
            </button>
            <button
              type="button"
              className={`rounded-xl px-3 py-2.5 text-sm font-semibold transition ${
                mode === 'register'
                  ? 'bg-[var(--color-surface-elevated)] text-[var(--color-ink)] shadow-sm'
                  : 'text-[var(--color-ink-muted)]'
              }`}
              onClick={() => {
                setMode('register')
                setErrors({})
              }}
            >
              Register
            </button>
          </div>

          <form className="space-y-4" onSubmit={onSubmit} noValidate>
            {mode === 'register' ? (
              <Input
                label="Name"
                name="name"
                type="text"
                autoComplete="name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                error={errors.name}
                placeholder="Alex Creator"
              />
            ) : null}
            <Input
              label="Email"
              name="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              error={errors.email}
              placeholder="you@example.com"
            />
            <Input
              label="Password"
              name="password"
              type="password"
              autoComplete={
                mode === 'register' ? 'new-password' : 'current-password'
              }
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              error={errors.password}
              placeholder="••••••••"
            />

            {error ? (
              <p className="rounded-xl bg-[var(--color-danger-soft)] px-3 py-2 text-sm text-[var(--color-danger)]">
                {error}
              </p>
            ) : null}

            <Button
              type="submit"
              className="w-full"
              size="lg"
              loading={submitting}
              disabled={!canSubmit}
            >
              {mode === 'login' ? 'Sign in' : 'Create account'}
            </Button>
          </form>

          <p className="mt-5 text-center text-sm text-[var(--color-ink-muted)]">
            {mode === 'login' ? (
              <>
                Need an account?{' '}
                <Link to="/register" className="font-semibold text-[var(--color-accent)]">
                  Register
                </Link>
              </>
            ) : (
              <>
                Already registered?{' '}
                <Link to="/login" className="font-semibold text-[var(--color-accent)]">
                  Sign in
                </Link>
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  )
}

export function RegisterPage() {
  return <LoginPage initialMode="register" />
}
