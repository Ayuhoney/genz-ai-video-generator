import { Film } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { Project } from '../types'
import { formatDate, formatDuration } from '../services/utils'

const statusStyles: Record<Project['status'], string> = {
  draft: 'bg-[var(--color-surface)] text-[var(--color-ink-muted)]',
  review: 'bg-amber-500/12 text-amber-800 dark:text-amber-300',
  confirmed: 'bg-sky-500/12 text-sky-800 dark:text-sky-300',
  producing: 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]',
  completed: 'bg-emerald-500/12 text-emerald-800 dark:text-emerald-300',
  failed: 'bg-[var(--color-danger-soft)] text-[var(--color-danger)]',
}

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link to={`/project/${project.id}`} className="gc-card group flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
          <Film className="h-5 w-5" />
        </div>
        <span
          className={`rounded-lg px-2.5 py-1 text-xs font-bold uppercase tracking-wide ${statusStyles[project.status]}`}
        >
          {project.status}
        </span>
      </div>
      <div>
        <h3 className="text-lg font-bold tracking-[-0.02em] text-[var(--color-ink)] transition group-hover:text-[var(--color-accent)]">
          {project.title}
        </h3>
        <p className="meta-text mt-2 line-clamp-2 leading-relaxed">
          {project.idea ?? project.concept ?? 'No description yet.'}
        </p>
      </div>
      <div className="meta-text mt-auto flex items-center justify-between border-t border-[var(--color-border)] pt-3">
        <span>{formatDuration(project.durationSeconds)}</span>
        <span>{formatDate(project.createdAt)}</span>
      </div>
    </Link>
  )
}
