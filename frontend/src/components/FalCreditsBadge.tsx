import { RefreshCw, Wallet } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getFalCredits } from '../services/api'
import type { FalCredits } from '../types'

export function FalCreditsBadge() {
  const [data, setData] = useState<FalCredits | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await getFalCredits())
    } catch {
      setData({
        available: false,
        balance: null,
        currency: 'USD',
        username: null,
        message: 'Unavailable',
      })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 60_000)
    return () => window.clearInterval(timer)
  }, [load])

  const balanceLabel =
    data?.available && data.balance != null
      ? `${data.currency === 'USD' ? '$' : ''}${Number(data.balance).toFixed(2)}`
      : '—'

  return (
    <button
      type="button"
      onClick={() => void load()}
      title={data?.message || 'fal.ai credits remaining'}
      className="hidden items-center gap-2.5 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3.5 py-2 text-left transition hover:border-[var(--color-accent)]/40 sm:inline-flex"
    >
      <Wallet className="h-4 w-4 shrink-0 text-[var(--color-accent)]" />
      <span className="leading-tight">
        <span className="block text-xs font-bold uppercase tracking-[0.08em] text-[var(--color-ink-muted)]">
          fal credits
        </span>
        <span className="block text-base font-bold tabular-nums text-[var(--color-ink)]">
          {loading ? '…' : balanceLabel}
        </span>
      </span>
      <RefreshCw
        className={`h-3.5 w-3.5 text-[var(--color-ink-muted)] ${loading ? 'animate-spin' : ''}`}
      />
    </button>
  )
}
