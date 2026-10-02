import { ArrowLeft, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Button } from '../components/Button'
import { ProgressBar, StatusBadge } from '../components/StatusBadge'
import { EmptyState, ErrorState, Skeleton, Spinner } from '../components/States'
import { useAsync } from '../hooks/useAsync'
import {
  getFinalVideoUrl,
  getProductionState,
  getProject,
  retryFailedItem,
} from '../services/api'
import { formatDate, formatDuration } from '../services/utils'
import type { ProductionStage, Scene } from '../types'

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
  const [workflowStatus, setWorkflowStatus] = useState<string>('pending')
  const [retryingKey, setRetryingKey] = useState<string | null>(null)
  const [retryError, setRetryError] = useState<string | null>(null)
  const [finalVideoUrl, setFinalVideoUrl] = useState<string | null>(null)

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
  }, [productionAsync.data])

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
        })
        .catch(() => {
          // Keep last known UI state; next poll retries.
        })
    }, POLL_MS)

    return () => window.clearInterval(timer)
  }, [projectId, workflowStatus])

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
      <div className="flex flex-col gap-4">
        <Link
          to="/dashboard"
          className="inline-flex w-fit items-center gap-2 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to dashboard
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              {project.title}
            </h1>
            <p className="mt-1 text-sm text-[var(--color-ink-muted)]">
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
          <p className="max-w-3xl text-sm text-[var(--color-ink-muted)]">
            {project.concept ?? project.idea}
          </p>
        ) : null}
      </div>

      {retryError ? (
        <ErrorState message={retryError} onRetry={() => setRetryError(null)} />
      ) : null}

      {finalVideoUrl ? (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Final video</h2>
          <div className="overflow-hidden rounded-2xl border border-[var(--color-border)] bg-black">
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
            className="inline-flex items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-2 text-sm font-medium hover:bg-[var(--color-accent-soft)]"
          >
            Download MP4
          </a>
        </section>
      ) : null}

      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Production pipeline</h2>
        <div className="overflow-x-auto pb-2">
          <ol className="flex min-w-max gap-3">
            {stages.map((stage, index) => (
              <li
                key={stage.id}
                className="w-44 shrink-0 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4"
              >
                <div className="mb-3 flex items-center justify-between gap-2">
                  <span className="text-xs font-medium uppercase tracking-wide text-[var(--color-ink-muted)]">
                    {index + 1}. {stage.label}
                  </span>
                </div>
                <div className="mb-3">
                  <StatusBadge status={stage.status} />
                </div>
                <ProgressBar value={stage.progress} status={stage.status} />
                <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
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
        <h2 className="text-lg font-semibold">Scenes</h2>
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
                    <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
                      {formatDuration(scene.durationSeconds)}
                    </p>
                  </div>
                  {scene.status === 'failed' ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      loading={retryingKey === `scene:${scene.id}`}
                      onClick={() => void onRetry('scene', scene.id)}
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                      Retry
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
