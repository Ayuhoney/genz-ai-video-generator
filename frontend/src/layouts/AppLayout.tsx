import { LogOut, Moon, Plus, Sun } from 'lucide-react'
import { Link, NavLink, Outlet } from 'react-router-dom'
import { Button } from '../components/Button'
import { FalCreditsBadge } from '../components/FalCreditsBadge'
import { useAuthContext } from '../hooks/useAuthContext'
import { useTheme } from '../hooks/useTheme'

export function AppLayout() {
  const { user, logout } = useAuthContext()
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="app-shell text-[var(--color-ink)]">
      <header className="sticky top-0 z-40 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)]/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3.5 sm:px-6">
          <div className="flex items-center gap-6 lg:gap-8">
            <Link to="/dashboard" className="group flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[var(--color-accent)] text-sm font-bold text-white">
                GC
              </span>
              <span className="leading-tight">
                <span className="block text-base font-bold tracking-[-0.02em] text-[var(--color-ink)] sm:text-lg">
                  GENZ CINE
                </span>
                <span className="block text-xs font-bold uppercase tracking-[0.1em] text-[var(--color-accent)]">
                  Cine AI
                </span>
              </span>
            </Link>
            <nav className="hidden items-center gap-1 text-[0.9375rem] font-semibold sm:flex">
              <NavLink
                to="/dashboard"
                className={({ isActive }) =>
                  `rounded-xl px-3.5 py-2 transition ${
                    isActive
                      ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                      : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
                  }`
                }
              >
                Dashboard
              </NavLink>
              <NavLink
                to="/create"
                className={({ isActive }) =>
                  `rounded-xl px-3.5 py-2 transition ${
                    isActive
                      ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                      : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
                  }`
                }
              >
                Create
              </NavLink>
            </nav>
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <FalCreditsBadge />
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
            <Link to="/create" className="hidden sm:inline-flex">
              <Button size="sm">
                <Plus className="h-4 w-4" />
                New Video
              </Button>
            </Link>
            <div className="hidden border-l border-[var(--color-border)] pl-3 text-right md:block">
              <div className="text-sm font-semibold text-[var(--color-ink)]">
                {user?.name}
              </div>
              <div className="max-w-[12rem] truncate text-sm text-[var(--color-ink-muted)]">
                {user?.email}
              </div>
            </div>
            <Button variant="ghost" size="sm" onClick={logout} aria-label="Log out">
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
        <Outlet />
      </main>
    </div>
  )
}
