import type { ProductionStatus } from '../types'

const styles: Record<ProductionStatus, string> = {
  pending: 'bg-[var(--color-surface)] text-[var(--color-ink-muted)]',
  running: 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]',
  completed: 'bg-emerald-500/12 text-emerald-800 dark:text-emerald-300',
  failed: 'bg-[var(--color-danger-soft)] text-[var(--color-danger)]',
}

export function StatusBadge({ status }: { status: ProductionStatus }) {
  return (
    <span
      className={`inline-flex rounded-lg px-2.5 py-1 text-xs font-bold uppercase tracking-wide ${styles[status]}`}
    >
      {status}
    </span>
  )
}

export function ProgressBar({
  value,
  status,
}: {
  value: number
  status: ProductionStatus
}) {
  const color =
    status === 'failed'
      ? 'bg-[var(--color-danger)]'
      : status === 'completed'
        ? 'bg-[var(--color-success)]'
        : 'bg-[var(--color-accent)]'

  return (
    <div className="h-1.5 w-full overflow-hidden rounded-lg bg-[var(--color-border)]">
      <div
        className={`h-full rounded-lg transition-all duration-500 ${color}`}
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  )
}
