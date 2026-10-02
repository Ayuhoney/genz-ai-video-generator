import { Plus } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Button } from '../components/Button'
import { ProjectCard } from '../components/ProjectCard'
import { EmptyState, ErrorState, Skeleton } from '../components/States'
import { useAsync } from '../hooks/useAsync'
import { getProjects } from '../services/api'

export function DashboardPage() {
  const navigate = useNavigate()
  const { data, error, isLoading, reload } = useAsync(getProjects, 'projects')

  return (
    <div className="space-y-8">
      <div className="anim-rise flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="gc-eyebrow">Your studio</p>
          <h1 className="page-title mt-2">Dashboard</h1>
          <p className="body-muted mt-3 max-w-lg">
            Projects, production status, and finished films — one destination.
          </p>
        </div>
        <Button onClick={() => navigate('/create')} size="lg">
          <Plus className="h-4 w-4" />
          New Video
        </Button>
      </div>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-48 rounded-[1.25rem]" />
          ))}
        </div>
      ) : null}

      {!isLoading && error ? (
        <ErrorState message={error} onRetry={() => void reload()} />
      ) : null}

      {!isLoading && !error && data && data.length === 0 ? (
        <EmptyState
          title="No projects yet"
          description="Create your first video idea and let Cine AI draft a full production plan."
          action={
            <Button onClick={() => navigate('/create')}>
              <Plus className="h-4 w-4" />
              New Video
            </Button>
          }
        />
      ) : null}

      {!isLoading && !error && data && data.length > 0 ? (
        <div className="anim-rise-delay grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      ) : null}
    </div>
  )
}
