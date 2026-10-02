import { useMemo, useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Clapperboard } from 'lucide-react'
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
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(15,118,110,0.18),_transparent_45%),radial-gradient(circle_at_bottom_right,_rgba(20,32,26,0.08),_transparent_40%)]" />
      <div className="relative w-full max-w-md rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-6 shadow-lg sm:p-8">
        <div className="mb-6 flex flex-col items-center text-center">
          <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--color-accent)] text-white">
            <Clapperboard className="h-6 w-6" />
          </span>
          <h1 className="text-2xl font-semibold">
            {mode === 'login' ? 'Welcome back' : 'Create account'}
          </h1>
          <p className="mt-1 text-sm text-[var(--color-ink-muted)]">
            {mode === 'login'
              ? 'Sign in to continue creating AI videos.'
              : 'Register to start generating videos.'}
          </p>
        </div>

        <div className="mb-5 grid grid-cols-2 gap-1 rounded-xl bg-[var(--color-surface)] p-1">
          <button
            type="button"
            className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
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
            className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
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
            <p className="rounded-lg bg-[var(--color-danger-soft)] px-3 py-2 text-sm text-[var(--color-danger)]">
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

        <p className="mt-4 text-center text-xs text-[var(--color-ink-muted)]">
          {mode === 'login' ? (
            <>
              Need an account?{' '}
              <Link to="/register" className="text-[var(--color-accent)]">
                Register
              </Link>
            </>
          ) : (
            <>
              Already registered?{' '}
              <Link to="/login" className="text-[var(--color-accent)]">
                Sign in
              </Link>
            </>
          )}
        </p>
      </div>
    </div>
  )
}

export function RegisterPage() {
  return <LoginPage initialMode="register" />
}
