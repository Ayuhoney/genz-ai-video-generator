import { Clapperboard, LogOut, Moon, Plus, Sun } from 'lucide-react'
import { Link, NavLink, Outlet } from 'react-router-dom'
import { Button } from '../components/Button'
import { useAuthContext } from '../hooks/useAuthContext'
import { useTheme } from '../hooks/useTheme'

export function AppLayout() {
  const { user, logout } = useAuthContext()
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="min-h-screen bg-[var(--color-surface)] text-[var(--color-ink)]">
      <header className="sticky top-0 z-40 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)]/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-6">
            <Link to="/dashboard" className="flex items-center gap-2 font-semibold">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--color-accent)] text-white">
                <Clapperboard className="h-5 w-5" />
              </span>
              <span className="hidden sm:inline">AI Video Platform</span>
            </Link>
            <nav className="flex items-center gap-1 text-sm">
              <NavLink
                to="/dashboard"
                className={({ isActive }) =>
                  `rounded-lg px-3 py-2 ${isActive ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]' : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'}`
                }
              >
                Dashboard
              </NavLink>
              <NavLink
                to="/create"
                className={({ isActive }) =>
                  `rounded-lg px-3 py-2 ${isActive ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]' : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'}`
                }
              >
                Create
              </NavLink>
            </nav>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={toggleTheme}
              aria-label="Toggle theme"
            >
              {theme === 'dark' ? (
                <Sun className="h-4 w-4" />
              ) : (
                <Moon className="h-4 w-4" />
              )}
            </Button>
            <Link
              to="/create"
              className="hidden items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-1.5 text-sm font-medium hover:bg-[var(--color-accent-soft)] sm:inline-flex"
            >
              <Plus className="h-4 w-4" />
              New Video
            </Link>
            <div className="hidden text-right text-xs text-[var(--color-ink-muted)] md:block">
              <div className="font-medium text-[var(--color-ink)]">{user?.name}</div>
              <div>{user?.email}</div>
            </div>
            <Button variant="ghost" size="sm" onClick={logout} aria-label="Log out">
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
        <Outlet />
      </main>
    </div>
  )
}
