import type { ProductionStatus } from '../types'

const styles: Record<ProductionStatus, string> = {
  pending: 'bg-slate-500/15 text-slate-700 dark:text-slate-300',
  running: 'bg-teal-500/15 text-teal-800 dark:text-teal-300',
  completed: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300',
  failed: 'bg-red-500/15 text-red-800 dark:text-red-300',
}

export function StatusBadge({ status }: { status: ProductionStatus }) {
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium capitalize ${styles[status]}`}
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
    <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--color-border)]">
      <div
        className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  )
}
