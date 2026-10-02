import { Clapperboard } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { Project } from '../types'
import { formatDate, formatDuration } from '../services/utils'

const statusStyles: Record<Project['status'], string> = {
  draft: 'bg-slate-500/15 text-slate-700 dark:text-slate-300',
  review: 'bg-amber-500/15 text-amber-800 dark:text-amber-300',
  confirmed: 'bg-sky-500/15 text-sky-800 dark:text-sky-300',
  producing: 'bg-teal-500/15 text-teal-800 dark:text-teal-300',
  completed: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300',
  failed: 'bg-red-500/15 text-red-800 dark:text-red-300',
}

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link
      to={`/project/${project.id}`}
      className="group flex flex-col gap-4 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-5 transition hover:border-[var(--color-accent)] hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
          <Clapperboard className="h-5 w-5" />
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-medium capitalize ${statusStyles[project.status]}`}
        >
          {project.status}
        </span>
      </div>
      <div>
        <h3 className="text-base font-semibold text-[var(--color-ink)] group-hover:text-[var(--color-accent)]">
          {project.title}
        </h3>
        <p className="mt-1 line-clamp-2 text-sm text-[var(--color-ink-muted)]">
          {project.idea ?? project.concept ?? 'No description yet.'}
        </p>
      </div>
      <div className="mt-auto flex items-center justify-between text-xs text-[var(--color-ink-muted)]">
        <span>{formatDuration(project.durationSeconds)}</span>
        <span>{formatDate(project.createdAt)}</span>
      </div>
    </Link>
  )
}
