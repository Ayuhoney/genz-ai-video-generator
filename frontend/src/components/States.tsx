import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from './Button'

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-[var(--color-ink-muted)]">
      <span className="h-8 w-8 animate-spin rounded-full border-2 border-[var(--color-accent)] border-r-transparent" />
      <p className="text-sm">{label}</p>
    </div>
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-lg bg-[var(--color-border)]/60 ${className}`}
    />
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-6 py-16 text-center">
      <Inbox className="h-10 w-10 text-[var(--color-ink-muted)]" />
      <h3 className="text-lg font-semibold text-[var(--color-ink)]">{title}</h3>
      <p className="max-w-md text-sm text-[var(--color-ink-muted)]">{description}</p>
      {action}
    </div>
  )
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string
  onRetry?: () => void
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-[var(--color-danger)]/30 bg-[var(--color-danger-soft)] px-6 py-12 text-center">
      <AlertTriangle className="h-8 w-8 text-[var(--color-danger)]" />
      <p className="max-w-md text-sm text-[var(--color-danger)]">{message}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          <RefreshCw className="h-4 w-4" />
          Retry
        </Button>
      ) : null}
    </div>
  )
}
