import { ArrowLeft, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Button } from '../components/Button'
import { ProgressBar, StatusBadge } from '../components/StatusBadge'
import { EmptyState, ErrorState, Skeleton, Spinner } from '../components/States'
import { useAsync } from '../hooks/useAsync'
import {
  getAssetSignedUrl,
  getFinalVideoUrl,
  getProductionState,
  getProject,
  listProjectAssets,
  regenerateProduction,
  retryFailedItem,
  type ProjectAsset,
} from '../services/api'
import { formatDate, formatDuration } from '../services/utils'
import type { ProductionStage, Scene } from '../types'

const POLL_MS = 2500

interface ClipCard {
  assetId: string
  shotId: string
  sceneId: string
  sceneTitle: string
  shotTitle: string
  duration: number | null
  url: string | null
}

export function ProjectPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = id ?? ''

  const projectAsync = useAsync(() => getProject(projectId), projectId)
  const productionAsync = useAsync(
    () => getProductionState(projectId),
    projectId,
  )

  const [stages, setStages] = useState<ProductionStage[]>(
    () => productionAsync.data?.stages ?? [],
  )
  const [scenes, setScenes] = useState<Scene[]>(
    () => productionAsync.data?.scenes ?? [],
  )
  const [shots, setShots] = useState<
    Array<{ id: string; sceneId: string; order: number; title: string; status: string }>
  >([])
  const [workflowStatus, setWorkflowStatus] = useState<string>('pending')
  const [retryingKey, setRetryingKey] = useState<string | null>(null)
  const [retryError, setRetryError] = useState<string | null>(null)
  const [finalVideoUrl, setFinalVideoUrl] = useState<string | null>(null)
  const [clips, setClips] = useState<ClipCard[]>([])
  const [clipsLoading, setClipsLoading] = useState(false)

  const loadClips = useCallback(async () => {
    if (!projectId) {
      return
    }
    setClipsLoading(true)
    try {
      const [{ assets }, state] = await Promise.all([
        listProjectAssets(projectId),
        getProductionState(projectId),
      ])
      setShots(
        (state.raw.shots || []).map((s) => ({
          id: s.id,
          sceneId: s.sceneId,
          order: s.order,
          title: s.title,
          status: s.status,
        })),
      )
      const sceneTitle = new Map(
        state.scenes.map((s) => [s.id, s.title] as const),
      )
      const shotMeta = new Map(
        (state.raw.shots || []).map((s) => [s.id, s] as const),
      )

      // Latest video asset per shot_id
      const byShot = new Map<string, ProjectAsset>()
      for (const asset of assets) {
        if (asset.type !== 'video' || !asset.shotId) {
          continue
        }
        byShot.set(asset.shotId, asset)
      }

      const cards: ClipCard[] = []
      for (const [shotId, asset] of byShot) {
        const meta = shotMeta.get(shotId)
        const sceneId = asset.sceneId || meta?.sceneId || ''
        let url: string | null = null
        try {
          const signed = await getAssetSignedUrl(projectId, asset.id)
          url = signed.url
        } catch {
          url = null
        }
        cards.push({
          assetId: asset.id,
          shotId,
          sceneId,
          sceneTitle: sceneTitle.get(sceneId) || sceneId || 'Scene',
          shotTitle: meta?.title || shotId,
          duration: asset.duration ?? null,
          url,
        })
      }
      cards.sort((a, b) => {
        const ao = shotMeta.get(a.shotId)?.order ?? 0
        const bo = shotMeta.get(b.shotId)?.order ?? 0
        if (a.sceneId !== b.sceneId) {
          return a.sceneId.localeCompare(b.sceneId)
        }
        return ao - bo
      })
      setClips(cards)
    } catch {
      // Keep last known clips.
    } finally {
      setClipsLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    if (
      !projectId ||
      (workflowStatus !== 'completed' && projectAsync.data?.status !== 'completed')
    ) {
      setFinalVideoUrl(null)
      return
    }
    void getFinalVideoUrl(projectId)
      .then((res) => setFinalVideoUrl(res.url))
      .catch(() => setFinalVideoUrl(null))
  }, [projectId, workflowStatus, projectAsync.data?.status])

  useEffect(() => {
    if (!productionAsync.data) {
      return
    }
    setStages(productionAsync.data.stages)
    setScenes(productionAsync.data.scenes)
    setWorkflowStatus(productionAsync.data.raw.status)
    setShots(
      (productionAsync.data.raw.shots || []).map((s) => ({
        id: s.id,
        sceneId: s.sceneId,
        order: s.order,
        title: s.title,
        status: s.status,
      })),
    )
  }, [productionAsync.data])

  useEffect(() => {
    if (!projectId) {
      return
    }
    void loadClips()
  }, [projectId, loadClips, workflowStatus])

  // Poll real production status every 2–3s until finished.
  useEffect(() => {
    if (!projectId) {
      return
    }
    const finished =
      workflowStatus === 'completed' || workflowStatus === 'failed'
    if (finished) {
      return
    }

    const timer = window.setInterval(() => {
      void getProductionState(projectId)
        .then((data) => {
          setStages(data.stages)
          setScenes(data.scenes)
          setWorkflowStatus(data.raw.status)
          setShots(
            (data.raw.shots || []).map((s) => ({
              id: s.id,
              sceneId: s.sceneId,
              order: s.order,
              title: s.title,
              status: s.status,
            })),
          )
          if (data.raw.status === 'completed') {
            void loadClips()
          }
        })
        .catch(() => {
          // Keep last known UI state; next poll retries.
        })
    }, POLL_MS)

    return () => window.clearInterval(timer)
  }, [projectId, workflowStatus, loadClips])

  const isBusy = useMemo(
    () =>
      workflowStatus === 'running' ||
      workflowStatus === 'paused' ||
      Boolean(retryingKey),
    [workflowStatus, retryingKey],
  )

  const onRetry = async (kind: 'stage' | 'scene', itemId: string) => {
    setRetryingKey(`${kind}:${itemId}`)
    setRetryError(null)
    try {
      const result = await retryFailedItem(projectId, kind, itemId)
      setStages(result.stages)
      setScenes(result.scenes)
      await productionAsync.reload()
    } catch (error) {
      setRetryError(
        error instanceof Error ? error.message : 'Retry failed.',
      )
    } finally {
      setRetryingKey(null)
    }
  }

  const onRegenerateShot = async (shotId: string) => {
    setRetryingKey(`shot:${shotId}`)
    setRetryError(null)
    try {
      await regenerateProduction(projectId, { shotIds: [shotId] })
      setWorkflowStatus('running')
      await productionAsync.reload()
    } catch (error) {
      setRetryError(
        error instanceof Error ? error.message : 'Clip regenerate failed.',
      )
    } finally {
      setRetryingKey(null)
    }
  }

  const onRegenerateScene = async (sceneId: string) => {
    setRetryingKey(`scene-regen:${sceneId}`)
    setRetryError(null)
    try {
      await regenerateProduction(projectId, { sceneIds: [sceneId] })
      setWorkflowStatus('running')
      await productionAsync.reload()
    } catch (error) {
      setRetryError(
        error instanceof Error ? error.message : 'Scene regenerate failed.',
      )
    } finally {
      setRetryingKey(null)
    }
  }

  if (!projectId) {
    return (
      <EmptyState
        title="Missing project"
        description="No project id was provided in the URL."
        action={
          <Link
            to="/dashboard"
            className="inline-flex items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-2 text-sm font-medium hover:bg-[var(--color-accent-soft)]"
          >
            Back to dashboard
          </Link>
        }
      />
    )
  }

  if (projectAsync.isLoading || productionAsync.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
        <Spinner label="Loading production dashboard…" />
      </div>
    )
  }

  if (projectAsync.error) {
    return (
      <ErrorState
        message={projectAsync.error}
        onRetry={() => void projectAsync.reload()}
      />
    )
  }

  if (productionAsync.error) {
    return (
      <ErrorState
        message={productionAsync.error}
        onRetry={() => void productionAsync.reload()}
      />
    )
  }

  const project = projectAsync.data
  if (!project) {
    return (
      <EmptyState
        title="Project not found"
        description="This project may have been removed."
        action={
          <Link
            to="/dashboard"
            className="inline-flex items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-2 text-sm font-medium hover:bg-[var(--color-accent-soft)]"
          >
            Back to dashboard
          </Link>
        }
      />
    )
  }

  return (
    <div className="space-y-8">
      <div className="anim-rise flex flex-col gap-4">
        <Link
          to="/dashboard"
          className="inline-flex w-fit items-center gap-2 text-sm font-medium text-[var(--color-ink-muted)] transition hover:text-[var(--color-accent)]"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to dashboard
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="gc-eyebrow">Production</p>
            <h1 className="page-title mt-1">{project.title}</h1>
            <p className="meta-text mt-2">
              {formatDuration(project.durationSeconds)} · Created{' '}
              {formatDate(project.createdAt)} · {project.language} ·{' '}
              {project.genre}
            </p>
          </div>
          <StatusBadge
            status={
              workflowStatus === 'completed'
                ? 'completed'
                : workflowStatus === 'failed'
                  ? 'failed'
                  : workflowStatus === 'running' ||
                      workflowStatus === 'paused' ||
                      project.status === 'producing' ||
                      project.status === 'confirmed'
                    ? 'running'
                    : 'pending'
            }
          />
        </div>
        {project.concept || project.idea ? (
          <p className="body-muted max-w-3xl">
            {project.concept ?? project.idea}
          </p>
        ) : null}
      </div>

      {retryError ? (
        <ErrorState message={retryError} onRetry={() => setRetryError(null)} />
      ) : null}

      {finalVideoUrl ? (
        <section className="anim-fade space-y-3">
          <h2 className="section-title">Final video</h2>
          <div className="overflow-hidden rounded-[1.35rem] border border-[var(--color-border)] bg-black">
            <video
              className="aspect-video w-full"
              src={finalVideoUrl}
              controls
              playsInline
            />
          </div>
          <a
            href={finalVideoUrl}
            download
            className="inline-flex items-center justify-center rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-2.5 text-sm font-semibold hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
          >
            Download MP4
          </a>
        </section>
      ) : null}

      <section className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="section-title">Clips</h2>
            <p className="meta-text mt-1">
              Preview each shot clip. Retry regenerates that clip and rebuilds the final cut.
            </p>
          </div>
          <Button
            size="sm"
            variant="secondary"
            loading={clipsLoading}
            onClick={() => void loadClips()}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh clips
          </Button>
        </div>
        {clipsLoading && clips.length === 0 ? (
          <Spinner label="Loading clips…" />
        ) : clips.length === 0 ? (
          <EmptyState
            title="No clips yet"
            description="Shot videos appear here once video generation finishes."
          />
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2">
            {clips.map((clip) => {
              const shotStatus =
                shots.find((s) => s.id === clip.shotId)?.status || 'completed'
              return (
                <li
                  key={clip.assetId}
                  className="overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)]"
                >
                  <div className="bg-black">
                    {clip.url ? (
                      <video
                        className="aspect-video w-full"
                        src={clip.url}
                        controls
                        playsInline
                        preload="metadata"
                      />
                    ) : (
                      <div className="flex aspect-video items-center justify-center text-sm text-[var(--color-ink-muted)]">
                        Preview unavailable
                      </div>
                    )}
                  </div>
                  <div className="space-y-3 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-semibold">{clip.shotTitle}</h3>
                      <StatusBadge
                        status={
                          shotStatus === 'failed'
                            ? 'failed'
                            : shotStatus === 'running' || shotStatus === 'pending'
                              ? mapClipStatus(shotStatus, isBusy)
                              : 'completed'
                        }
                      />
                    </div>
                    <p className="meta-text">
                      {clip.sceneTitle}
                      {clip.duration != null
                        ? ` · ${formatDuration(Math.round(clip.duration))}`
                        : ''}
                    </p>
                    <Button
                      size="sm"
                      variant="secondary"
                      className="w-full"
                      disabled={isBusy}
                      loading={retryingKey === `shot:${clip.shotId}`}
                      onClick={() => void onRegenerateShot(clip.shotId)}
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                      Retry clip
                    </Button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section className="space-y-4">
        <h2 className="section-title">Production pipeline</h2>
        <div className="overflow-x-auto pb-2">
          <ol className="flex min-w-max gap-3">
            {stages.map((stage, index) => (
              <li
                key={stage.id}
                className="w-44 shrink-0 rounded-[1.25rem] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4"
              >
                <div className="mb-3 flex items-center justify-between gap-2">
                  <span className="text-sm font-bold uppercase tracking-wide text-[var(--color-ink-muted)]">
                    {index + 1}. {stage.label}
                  </span>
                </div>
                <div className="mb-3">
                  <StatusBadge status={stage.status} />
                </div>
                <ProgressBar value={stage.progress} status={stage.status} />
                <p className="meta-text mt-2">
                  {stage.progress}%
                </p>
                {stage.status === 'failed' ? (
                  <Button
                    className="mt-3 w-full"
                    size="sm"
                    variant="secondary"
                    loading={retryingKey === `stage:${stage.id}`}
                    onClick={() => void onRetry('stage', stage.id)}
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    Retry
                  </Button>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="section-title">Scenes</h2>
        {scenes.length === 0 ? (
          <EmptyState
            title="No scenes yet"
            description="Scene status will appear once production begins."
          />
        ) : (
          <ul className="space-y-3">
            {scenes.map((scene) => (
              <li
                key={scene.id}
                className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <h3 className="font-medium">
                        Scene {scene.order}: {scene.title}
                      </h3>
                      <StatusBadge status={scene.status} />
                    </div>
                    <p className="text-sm text-[var(--color-ink-muted)]">
                      {scene.description}
                    </p>
                    <p className="meta-text mt-2">
                      {formatDuration(scene.durationSeconds)}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col gap-2 sm:items-end">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={isBusy}
                      loading={retryingKey === `scene-regen:${scene.id}`}
                      onClick={() => void onRegenerateScene(scene.id)}
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                      Retry scene
                    </Button>
                    {scene.status === 'failed' ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        loading={retryingKey === `scene:${scene.id}`}
                        onClick={() => void onRetry('scene', scene.id)}
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                        Resume failed
                      </Button>
                    ) : null}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function mapClipStatus(
  shotStatus: string,
  busy: boolean,
): 'pending' | 'running' | 'completed' | 'failed' {
  if (shotStatus === 'failed') {
    return 'failed'
  }
  if (shotStatus === 'running' || (busy && shotStatus === 'pending')) {
    return 'running'
  }
  if (shotStatus === 'completed') {
    return 'completed'
  }
  return 'pending'
}
