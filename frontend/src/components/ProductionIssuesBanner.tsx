import {
  AlertTriangle,
  ImagePlus,
  RefreshCw,
  Sparkles,
  ShieldAlert,
} from 'lucide-react'
import { useState } from 'react'
import { Button } from './Button'
import type { FixShotMode, ProductionIssue } from '../types'

interface ProductionIssuesBannerProps {
  issues: ProductionIssue[]
  retryingKey?: string | null
  /** Optional shotId → human label (e.g. "Shot 2 · Scene title"). */
  shotLabels?: Record<string, string>
  onFixShot: (shotId: string, mode: FixShotMode, guidance?: string) => void
  onResumeStuck?: () => void
}

export function ProductionIssuesBanner({
  issues,
  retryingKey = null,
  shotLabels = {},
  onFixShot,
  onResumeStuck,
}: ProductionIssuesBannerProps) {
  const [guidanceByShot, setGuidanceByShot] = useState<Record<string, string>>(
    {},
  )

  if (issues.length === 0) {
    return null
  }

  const stuck = issues.filter((i) => i.code === 'stuck')
  const rest = issues.filter((i) => i.code !== 'stuck')
  const policy = rest.filter(
    (i) => i.code === 'content_policy' || i.code === 'needs_new_image',
  )
  const resumeBusy = retryingKey === 'resume:stuck'

  return (
    <section className="anim-rise space-y-3 rounded-[1.25rem] border border-amber-500/35 bg-amber-500/8 p-4">
      {stuck.length > 0 ? (
        <div className="rounded-xl border border-amber-500/40 bg-[var(--color-surface-elevated)] p-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <ShieldAlert className="h-4 w-4 text-amber-700 dark:text-amber-300" />
                <p className="text-sm font-semibold text-[var(--color-ink)]">
                  Production looks stuck
                </p>
                <span className="rounded-md bg-amber-500/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-800 dark:text-amber-200">
                  Resume
                </span>
              </div>
              <p className="mt-1.5 text-sm text-[var(--color-ink-muted)]">
                {stuck[0]?.message}
              </p>
            </div>
            {onResumeStuck ? (
              <Button
                size="sm"
                onClick={onResumeStuck}
                loading={resumeBusy}
                className="shrink-0"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Resume from here
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {rest.length > 0 ? (
        <>
          <div className="flex items-start gap-3">
            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-700 dark:text-amber-300" />
            <div className="min-w-0 flex-1">
              <h2 className="text-base font-bold text-[var(--color-ink)]">
                Action needed on {rest.length} shot
                {rest.length === 1 ? '' : 's'}
              </h2>
              <p className="mt-1 text-sm text-[var(--color-ink-muted)]">
                {policy.length > 0
                  ? 'Clips use a safe camera-only prompt now. If one still fails, try AI auto-fix, add your own camera/mood notes, or make a new still.'
                  : 'Some steps failed. Retry is OK for temporary errors.'}
              </p>
            </div>
          </div>

          <ul className="space-y-2">
            {rest.map((issue) => {
              const key = issue.shotId || issue.sceneId || issue.jobType
              const isPolicy =
                issue.code === 'content_policy' ||
                issue.code === 'needs_new_image'
              const shotId = issue.shotId || ''
              const busy = retryingKey === `shot:${shotId}`
              const guidance = guidanceByShot[shotId] || ''

              return (
                <li
                  key={`${issue.jobType}:${key}`}
                  className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-3"
                >
                  <div className="flex flex-col gap-3">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <AlertTriangle
                            className={`h-4 w-4 ${isPolicy ? 'text-amber-600' : 'text-[var(--color-danger)]'}`}
                          />
                          <p className="text-sm font-semibold text-[var(--color-ink)]">
                            {shotId
                              ? shotLabels[shotId] ||
                                `Shot ${shotId.slice(0, 8)}`
                              : issue.sceneId
                                ? `Scene ${issue.sceneId.slice(0, 8)}`
                                : issue.jobType}
                          </p>
                          <span className="rounded-md bg-[var(--color-accent-soft)] px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-[var(--color-accent)]">
                            {isPolicy ? 'Safety block' : 'Retryable'}
                          </span>
                        </div>
                        <p className="mt-1.5 text-sm text-[var(--color-ink-muted)]">
                          {issue.message}
                        </p>
                      </div>
                      {shotId ? (
                        <div className="flex shrink-0 flex-col gap-2 sm:items-end">
                          {isPolicy ? (
                            <>
                              <Button
                                size="sm"
                                onClick={() => onFixShot(shotId, 'auto_fix')}
                                loading={busy}
                              >
                                <Sparkles className="h-3.5 w-3.5" />
                                AI auto-fix
                              </Button>
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => onFixShot(shotId, 'new_still')}
                                loading={busy}
                              >
                                <ImagePlus className="h-3.5 w-3.5" />
                                New still + clip
                              </Button>
                            </>
                          ) : (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => onFixShot(shotId, 'retry')}
                              loading={busy}
                            >
                              <RefreshCw className="h-3.5 w-3.5" />
                              Retry clip
                            </Button>
                          )}
                        </div>
                      ) : null}
                    </div>

                    {shotId ? (
                      <div className="space-y-2 border-t border-[var(--color-border)] pt-3">
                        <label
                          className="block text-xs font-semibold text-[var(--color-ink-muted)]"
                          htmlFor={`guidance-${shotId}`}
                        >
                          Optional: change camera / mood (no weapons or story)
                        </label>
                        <textarea
                          id={`guidance-${shotId}`}
                          className="min-h-[4.5rem] w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-ink)] outline-none focus:border-[var(--color-accent)]"
                          placeholder="e.g. Slow orbit around the character, soft studio light, dust in air"
                          value={guidance}
                          onChange={(e) =>
                            setGuidanceByShot((prev) => ({
                              ...prev,
                              [shotId]: e.target.value,
                            }))
                          }
                        />
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={!guidance.trim()}
                          loading={busy}
                          onClick={() =>
                            onFixShot(shotId, 'guided', guidance.trim())
                          }
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                          Retry with my notes
                        </Button>
                      </div>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ul>
        </>
      ) : null}

      {policy.length > 0 ? (
        <p className="text-xs text-[var(--color-ink-muted)]">
          Tip: video prompts are camera-only now. Soft stills (emotion, dialogue,
          travel) pass more often than weapons or fight close-ups.
        </p>
      ) : null}
    </section>
  )
}
