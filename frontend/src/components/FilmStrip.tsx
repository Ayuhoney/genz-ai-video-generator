import { ImagePlus, RefreshCw, Sparkles, X } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button } from './Button'
import { StatusBadge } from './StatusBadge'
import type { ProductionIssueCode } from '../types'

export type FilmFrameKind = 'image' | 'video'

export interface FilmFrame {
  id: string
  kind: FilmFrameKind
  label: string
  sublabel: string
  url: string | null
  status?: 'pending' | 'running' | 'completed' | 'failed'
  sceneId?: string
  shotId?: string | null
  retryKind?: 'scene' | 'shot'
  retryId?: string
  /** When set, show a clearer CTA than plain "Retry". */
  issueCode?: ProductionIssueCode
  issueMessage?: string
}

interface FilmStripProps {
  frames: FilmFrame[]
  loading?: boolean
  retryingKey?: string | null
  onRetry?: (kind: 'scene' | 'shot', id: string) => void
  onRegenerateStill?: (shotId: string) => void
  onAutoFix?: (shotId: string) => void
}

export function FilmStrip({
  frames,
  loading = false,
  retryingKey = null,
  onRetry,
  onRegenerateStill,
  onAutoFix,
}: FilmStripProps) {
  const [active, setActive] = useState<FilmFrame | null>(null)

  if (loading && frames.length === 0) {
    return (
      <p className="meta-text">Loading previews…</p>
    )
  }

  if (frames.length === 0) {
    return (
      <p className="meta-text">
        Stills and clips appear here as soon as they are generated.
      </p>
    )
  }

  return (
    <>
      <div className="film-strip-scroll -mx-1 overflow-x-auto px-1 pb-3 pt-2">
        <ol className="film-strip flex min-w-max items-end gap-3">
          {frames.map((frame, index) => (
            <li key={frame.id} className="film-frame group relative shrink-0">
              <button
                type="button"
                className="film-frame-btn block w-[9.5rem] overflow-hidden rounded-xl border border-[var(--color-border)] bg-black text-left shadow-sm outline-none transition duration-200 ease-out hover:z-20 hover:scale-[1.12] hover:border-[var(--color-accent)] hover:shadow-xl focus-visible:z-20 focus-visible:scale-[1.12] focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
                onClick={() => setActive(frame)}
                aria-label={`Open ${frame.label}`}
              >
                <div className="relative aspect-video w-full bg-black">
                  {frame.url ? (
                    frame.kind === 'video' ? (
                      <video
                        className="h-full w-full object-cover"
                        src={frame.url}
                        muted
                        playsInline
                        preload="metadata"
                      />
                    ) : (
                      <img
                        className="h-full w-full object-cover"
                        src={frame.url}
                        alt={frame.label}
                        loading="lazy"
                      />
                    )
                  ) : (
                    <div className="flex h-full items-center justify-center px-2 text-center text-[11px] text-white/70">
                      Waiting…
                    </div>
                  )}
                  <span className="absolute left-1.5 top-1.5 rounded bg-black/65 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
                    {frame.kind === 'video' ? 'Clip' : 'Still'} {index + 1}
                  </span>
                </div>
                <div className="space-y-1 bg-[var(--color-surface-elevated)] px-2.5 py-2">
                  <p className="truncate text-xs font-semibold text-[var(--color-ink)]">
                    {frame.label}
                  </p>
                  <p className="truncate text-[11px] text-[var(--color-ink-muted)]">
                    {frame.sublabel}
                  </p>
                  {frame.status ? (
                    <StatusBadge status={frame.status} />
                  ) : null}
                  {frame.issueMessage && frame.status === 'failed' ? (
                    <p className="line-clamp-3 text-[10px] leading-snug text-amber-800 dark:text-amber-200">
                      {frame.issueMessage}
                    </p>
                  ) : null}
                </div>
              </button>
              {frame.status === 'failed' &&
              frame.shotId &&
              frame.kind === 'video' &&
              (frame.issueCode === 'content_policy' ||
                frame.issueCode === 'needs_new_image') ? (
                <div className="mt-2 flex w-full flex-col gap-1.5">
                  {onAutoFix ? (
                    <Button
                      size="sm"
                      className="w-full"
                      loading={retryingKey === `shot:${frame.shotId}`}
                      onClick={(event) => {
                        event.stopPropagation()
                        onAutoFix(frame.shotId!)
                      }}
                    >
                      <Sparkles className="h-3.5 w-3.5" />
                      AI auto-fix
                    </Button>
                  ) : null}
                  {onRegenerateStill ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      className="w-full"
                      loading={retryingKey === `shot:${frame.shotId}`}
                      onClick={(event) => {
                        event.stopPropagation()
                        onRegenerateStill(frame.shotId!)
                      }}
                    >
                      <ImagePlus className="h-3.5 w-3.5" />
                      New still + clip
                    </Button>
                  ) : null}
                </div>
              ) : frame.retryKind &&
                frame.retryId &&
                onRetry &&
                !(
                  frame.kind === 'image' &&
                  (frame.issueCode === 'content_policy' ||
                    frame.issueCode === 'needs_new_image')
                ) ? (
                <Button
                  size="sm"
                  variant="secondary"
                  className="mt-2 w-full"
                  loading={retryingKey === `${frame.retryKind}:${frame.retryId}`}
                  onClick={(event) => {
                    event.stopPropagation()
                    onRetry(frame.retryKind!, frame.retryId!)
                  }}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  {frame.status === 'failed'
                    ? frame.kind === 'video'
                      ? 'Retry clip'
                      : 'Retry still'
                    : 'Retry'}
                </Button>
              ) : null}
            </li>
          ))}
        </ol>
      </div>

      {active ? (
        <PreviewModal frame={active} onClose={() => setActive(null)} />
      ) : null}
    </>
  )
}

function PreviewModal({
  frame,
  onClose,
}: {
  frame: FilmFrame
  onClose: () => void
}) {
  const titleId = useId()

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [onClose])

  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4 backdrop-blur-[2px]"
      role="presentation"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative w-full max-w-4xl overflow-hidden rounded-2xl border border-white/15 bg-black/85 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-white/10 px-4 py-3">
          <div>
            <p id={titleId} className="text-sm font-semibold text-white">
              {frame.label}
            </p>
            <p className="text-xs text-white/65">{frame.sublabel}</p>
          </div>
          <button
            type="button"
            className="rounded-lg p-1.5 text-white/80 transition hover:bg-white/10 hover:text-white"
            onClick={onClose}
            aria-label="Close preview"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="bg-black">
          {frame.url ? (
            frame.kind === 'video' ? (
              <video
                className="max-h-[75vh] w-full"
                src={frame.url}
                controls
                autoPlay
                playsInline
              />
            ) : (
              <img
                className="max-h-[75vh] w-full object-contain"
                src={frame.url}
                alt={frame.label}
              />
            )
          ) : (
            <div className="flex aspect-video items-center justify-center text-sm text-white/70">
              Preview unavailable
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
