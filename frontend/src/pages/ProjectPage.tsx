import { ArrowLeft, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Button } from '../components/Button'
import { FilmStrip, type FilmFrame } from '../components/FilmStrip'
import { ProductionIssuesBanner } from '../components/ProductionIssuesBanner'
import { ProgressBar, StatusBadge } from '../components/StatusBadge'
import { EmptyState, ErrorState, Skeleton, Spinner } from '../components/States'
import { useAsync } from '../hooks/useAsync'
import {
  fixShotProduction,
  getAssetSignedUrl,
  getFinalVideoUrl,
  getProductionState,
  getProject,
  listProjectAssets,
  regenerateProduction,
  resumeMissingProduction,
  retryFailedItem,
  type ProjectAsset,
} from '../services/api'
import { formatDate, formatDuration } from '../services/utils'
import type {
  FixShotMode,
  ProductionIssue,
  ProductionStage,
  Scene,
} from '../types'

const POLL_MS = 2500

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
    Array<{
      id: string
      sceneId: string
      order: number
      title: string
      status: string
    }>
  >([])
  const [workflowStatus, setWorkflowStatus] = useState<string>('pending')
  const [retryingKey, setRetryingKey] = useState<string | null>(null)
  const [retryError, setRetryError] = useState<string | null>(null)
  const [finalVideoUrl, setFinalVideoUrl] = useState<string | null>(null)
  const [frames, setFrames] = useState<FilmFrame[]>([])
  const [framesLoading, setFramesLoading] = useState(false)
  const [issues, setIssues] = useState<ProductionIssue[]>([])

  const loadFilmstrip = useCallback(async () => {
    if (!projectId) {
      return
    }
    setFramesLoading(true)
    try {
      const [{ assets }, state] = await Promise.all([
        listProjectAssets(projectId),
        getProductionState(projectId),
      ])
      const nextShots = (state.raw.shots || []).map((s) => ({
        id: s.id,
        sceneId: s.sceneId,
        order: s.order,
        title: s.title,
        status: s.status,
      }))
      setShots(nextShots)

      const sceneById = new Map(state.scenes.map((s) => [s.id, s] as const))
      const shotById = new Map(nextShots.map((s) => [s.id, s] as const))
      const issueByShot = new Map(
        (state.raw.issues || [])
          .filter((issue) => issue.shotId)
          .map((issue) => [String(issue.shotId), issue] as const),
      )
      setIssues(state.raw.issues || [])

      const latestImageByShot = new Map<string, ProjectAsset>()
      const latestVideoByShot = new Map<string, ProjectAsset>()
      for (const asset of assets) {
        if (asset.type === 'image' && asset.shotId) {
          latestImageByShot.set(asset.shotId, asset)
        }
        if (asset.type === 'video' && asset.shotId) {
          latestVideoByShot.set(asset.shotId, asset)
        }
      }

      const urlCache = new Map<string, string | null>()
      const resolveUrl = async (assetId: string) => {
        if (urlCache.has(assetId)) {
          return urlCache.get(assetId) ?? null
        }
        try {
          const signed = await getAssetSignedUrl(projectId, assetId)
          urlCache.set(assetId, signed.url)
          return signed.url
        } catch {
          urlCache.set(assetId, null)
          return null
        }
      }

      const nextFrames: FilmFrame[] = []
      const orderedScenes = [...state.scenes].sort((a, b) => a.order - b.order)

      for (const scene of orderedScenes) {
        const sceneShots = nextShots
          .filter((s) => s.sceneId === scene.id)
          .sort((a, b) => a.order - b.order)
        for (const shot of sceneShots) {
          const issue = issueByShot.get(shot.id)
          const image = latestImageByShot.get(shot.id)
          const video = latestVideoByShot.get(shot.id)
          const imageStatus = mapUiStatus(
            image ? 'completed' : issue ? 'failed' : shot.status,
          )
          const videoStatus = mapUiStatus(
            video ? 'completed' : issue ? 'failed' : shot.status,
          )
          nextFrames.push({
            id: image ? `image:${image.id}` : `image-placeholder:${shot.id}`,
            kind: 'image',
            label: shot.title || `Shot ${shot.order}`,
            sublabel: `${sceneById.get(scene.id)?.title || scene.title} · Still`,
            url: image ? await resolveUrl(image.id) : null,
            status: imageStatus,
            sceneId: scene.id,
            shotId: shot.id,
            retryKind: 'shot',
            retryId: shot.id,
            issueCode: image ? undefined : issue?.code,
            issueMessage:
              !image && issue && issue.code === 'needs_new_image'
                ? issue.message
                : undefined,
          })
          nextFrames.push({
            id: video ? `video:${video.id}` : `video-placeholder:${shot.id}`,
            kind: 'video',
            label: shot.title || `Shot ${shot.order}`,
            sublabel: `${sceneById.get(scene.id)?.title || scene.title} · Clip`,
            url: video ? await resolveUrl(video.id) : null,
            status: videoStatus,
            sceneId: scene.id,
            shotId: shot.id,
            retryKind: 'shot',
            retryId: shot.id,
            issueCode: video ? undefined : issue?.code,
            issueMessage: video ? undefined : issue?.message,
          })
        }

        // If shots list empty but we somehow have orphan videos for scene
        if (sceneShots.length === 0) {
          for (const [shotId, video] of latestVideoByShot) {
            if (video.sceneId !== scene.id) {
              continue
            }
            const meta = shotById.get(shotId)
            nextFrames.push({
              id: `video:${video.id}`,
              kind: 'video',
              label: meta?.title || shotId,
              sublabel: `${scene.title} · Clip`,
              url: await resolveUrl(video.id),
              status: mapUiStatus(meta?.status || 'completed'),
              sceneId: scene.id,
              shotId,
              retryKind: 'shot',
              retryId: shotId,
            })
          }
        }
      }

      setFrames(nextFrames)
    } catch {
      // Keep last frames.
    } finally {
      setFramesLoading(false)
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
    setIssues(productionAsync.data.raw.issues || [])
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
    void loadFilmstrip()
  }, [projectId, loadFilmstrip, workflowStatus])

  // Poll production + filmstrip while running so stills/clips appear live.
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
          setIssues(data.raw.issues || [])
          setShots(
            (data.raw.shots || []).map((s) => ({
              id: s.id,
              sceneId: s.sceneId,
              order: s.order,
              title: s.title,
              status: s.status,
            })),
          )
        })
        .catch(() => undefined)
      void loadFilmstrip()
    }, POLL_MS)

    return () => window.clearInterval(timer)
  }, [projectId, workflowStatus, loadFilmstrip])

  const onRetryFailed = async (kind: 'stage' | 'scene', itemId: string) => {
    setRetryingKey(`${kind}:${itemId}`)
    setRetryError(null)
    try {
      const result = await retryFailedItem(projectId, kind, itemId)
      setStages(result.stages)
      setScenes(result.scenes)
      setWorkflowStatus('running')
      await productionAsync.reload()
      void loadFilmstrip()
    } catch (error) {
      setRetryError(error instanceof Error ? error.message : 'Retry failed.')
    } finally {
      setRetryingKey(null)
    }
  }

  const onFilmRetry = async (kind: 'scene' | 'shot', id: string) => {
    setRetryingKey(`${kind}:${id}`)
    setRetryError(null)
    try {
      if (kind === 'scene') {
        await regenerateProduction(projectId, { sceneIds: [id] })
      } else {
        await regenerateProduction(projectId, { shotIds: [id] })
      }
      setWorkflowStatus('running')
      await productionAsync.reload()
      void loadFilmstrip()
    } catch (error) {
      setRetryError(
        error instanceof Error ? error.message : 'Regenerate failed.',
      )
    } finally {
      setRetryingKey(null)
    }
  }

  const onFixShot = async (
    shotId: string,
    mode: FixShotMode,
    guidance?: string,
  ) => {
    setRetryingKey(`shot:${shotId}`)
    setRetryError(null)
    try {
      await fixShotProduction(projectId, { shotId, mode, guidance })
      setWorkflowStatus('running')
      await productionAsync.reload()
      void loadFilmstrip()
    } catch (error) {
      setRetryError(
        error instanceof Error ? error.message : 'Fix failed.',
      )
    } finally {
      setRetryingKey(null)
    }
  }

  const onRegenerateStill = async (shotId: string) => {
    await onFixShot(shotId, 'new_still')
  }

  const onResumeStuck = async () => {
    setRetryingKey('resume:stuck')
    setRetryError(null)
    try {
      await resumeMissingProduction(projectId, { confirm: true, assemble: true })
      setWorkflowStatus('running')
      await productionAsync.reload()
      void loadFilmstrip()
    } catch (error) {
      setRetryError(
        error instanceof Error
          ? error.message
          : 'Could not resume stuck production.',
      )
    } finally {
      setRetryingKey(null)
    }
  }

  const issueShotIds = useMemo(
    () => new Set(issues.map((i) => i.shotId).filter(Boolean) as string[]),
    [issues],
  )

  const shotLabels = useMemo(() => {
    const sceneTitle = new Map(scenes.map((s) => [s.id, s.title] as const))
    const labels: Record<string, string> = {}
    for (const shot of shots) {
      const scene = sceneTitle.get(shot.sceneId)
      labels[shot.id] = scene
        ? `${shot.title || `Shot ${shot.order}`} · ${scene}`
        : shot.title || `Shot ${shot.order}`
    }
    return labels
  }, [scenes, shots])

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
              workflowStatus === 'completed' || project.status === 'completed'
                ? 'completed'
                : workflowStatus === 'failed' || project.status === 'failed'
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

      <ProductionIssuesBanner
        issues={issues}
        retryingKey={retryingKey}
        shotLabels={shotLabels}
        onFixShot={(shotId, mode, guidance) =>
          void onFixShot(shotId, mode, guidance)
        }
        onResumeStuck={() => void onResumeStuck()}
      />

      <section className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="section-title">Storyboard & clips</h2>
            <p className="meta-text mt-1">
              Safety blocks need a new still. Temporary fails can use Retry clip.
            </p>
          </div>
          <Button
            size="sm"
            variant="secondary"
            loading={framesLoading}
            onClick={() => void loadFilmstrip()}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </Button>
        </div>
        <FilmStrip
          frames={frames}
          loading={framesLoading}
          retryingKey={retryingKey}
          onRetry={(kind, id) => void onFilmRetry(kind, id)}
          onRegenerateStill={(shotId) => void onRegenerateStill(shotId)}
          onAutoFix={(shotId) => void onFixShot(shotId, 'auto_fix')}
        />
      </section>

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
                <p className="meta-text mt-2">{stage.progress}%</p>
                {stage.status === 'failed' ? (
                  <Button
                    className="mt-3 w-full"
                    size="sm"
                    variant="secondary"
                    loading={retryingKey === `stage:${stage.id}`}
                    onClick={() => void onRetryFailed('stage', stage.id)}
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
            {scenes.map((scene) => {
              const sceneShots = shots.filter((s) => s.sceneId === scene.id)
              const hasFailedShot =
                sceneShots.some((s) => s.status === 'failed') ||
                sceneShots.some((s) => issueShotIds.has(s.id))
              const sceneIssues = issues.filter((i) => i.sceneId === scene.id)
              const hasPolicy = sceneIssues.some(
                (i) =>
                  i.code === 'content_policy' || i.code === 'needs_new_image',
              )
              return (
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
                        <StatusBadge
                          status={
                            hasFailedShot || scene.status === 'failed'
                              ? 'failed'
                              : scene.status
                          }
                        />
                      </div>
                      <p className="text-sm text-[var(--color-ink-muted)]">
                        {scene.description}
                      </p>
                      <p className="meta-text mt-2">
                        {formatDuration(scene.durationSeconds)}
                        {sceneShots.length
                          ? ` · ${sceneShots.length} shot${sceneShots.length === 1 ? '' : 's'}`
                          : ''}
                      </p>
                      {hasPolicy ? (
                        <p className="mt-2 text-sm text-amber-800 dark:text-amber-200">
                          Safety block on this scene — use &quot;New still +
                          clip&quot; on the failed shots above, not blind Retry.
                        </p>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 flex-col gap-2 sm:items-end">
                      <Button
                        size="sm"
                        variant="secondary"
                        loading={retryingKey === `scene:${scene.id}`}
                        onClick={() => void onFilmRetry('scene', scene.id)}
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                        {hasPolicy ? 'Regen scene stills' : 'Retry scene'}
                      </Button>
                      {(scene.status === 'failed' || hasFailedShot) &&
                        !hasPolicy && (
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={retryingKey === `resume:${scene.id}`}
                          onClick={() => void onRetryFailed('scene', scene.id)}
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                          Resume failed
                        </Button>
                      )}
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}

function mapUiStatus(
  value: string | undefined,
): 'pending' | 'running' | 'completed' | 'failed' {
  if (value === 'completed' || value === 'failed' || value === 'running') {
    return value
  }
  if (value === 'paused' || value === 'retrying') {
    return 'running'
  }
  return 'pending'
}
