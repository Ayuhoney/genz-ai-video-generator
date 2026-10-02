import type { ButtonHTMLAttributes, ReactNode } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
  children: ReactNode
}

const variants: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary:
    'bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-60 shadow-[0_10px_24px_-12px_rgba(225,29,46,0.75)]',
  secondary:
    'bg-[var(--color-surface-elevated)] text-[var(--color-ink)] border border-[var(--color-border)] hover:border-[var(--color-ink)]/20 hover:bg-[var(--color-surface)]',
  ghost:
    'bg-transparent text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] hover:bg-[var(--color-accent-soft)]',
  danger:
    'bg-[var(--color-danger)] text-white hover:opacity-90 disabled:opacity-60',
}

const sizes: Record<NonNullable<ButtonProps['size']>, string> = {
  sm: 'px-4 py-2.5 text-sm',
  md: 'px-5 py-3 text-base',
  lg: 'px-6 py-3.5 text-base',
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  disabled,
  children,
  className = '',
  ...props
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-xl font-semibold tracking-tight transition duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)] disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent" />
      ) : null}
      {children}
    </button>
  )
}
