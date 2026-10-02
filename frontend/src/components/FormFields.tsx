import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'

const fieldClass =
  'w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-2 text-[var(--color-ink)] outline-none transition placeholder:text-[var(--color-ink-muted)] focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent)]/20'

interface FieldWrapProps {
  label: string
  error?: string
  htmlFor?: string
  children: ReactNode
}

export function FieldWrap({ label, error, htmlFor, children }: FieldWrapProps) {
  return (
    <label className="block space-y-1.5" htmlFor={htmlFor}>
      <span className="text-sm font-medium text-[var(--color-ink)]">{label}</span>
      {children}
      {error ? (
        <span className="block text-sm text-[var(--color-danger)]">{error}</span>
      ) : null}
    </label>
  )
}

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  error?: string
}

export function Input({ label, error, id, className = '', ...props }: InputProps) {
  const inputId = id ?? props.name
  return (
    <FieldWrap label={label} error={error} htmlFor={inputId}>
      <input id={inputId} className={`${fieldClass} ${className}`} {...props} />
    </FieldWrap>
  )
}

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string
  error?: string
}

export function Textarea({
  label,
  error,
  id,
  className = '',
  ...props
}: TextareaProps) {
  const inputId = id ?? props.name
  return (
    <FieldWrap label={label} error={error} htmlFor={inputId}>
      <textarea
        id={inputId}
        className={`${fieldClass} min-h-28 resize-y ${className}`}
        {...props}
      />
    </FieldWrap>
  )
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string
  error?: string
  options: readonly string[]
}

export function Select({
  label,
  error,
  options,
  id,
  className = '',
  ...props
}: SelectProps) {
  const inputId = id ?? props.name
  return (
    <FieldWrap label={label} error={error} htmlFor={inputId}>
      <select id={inputId} className={`${fieldClass} ${className}`} {...props}>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </FieldWrap>
  )
}
