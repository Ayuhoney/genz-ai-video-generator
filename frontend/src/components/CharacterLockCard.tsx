import { ImagePlus, Lock, Unlock, X } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'
import { uploadCharacterRef } from '../services/api'
import type { Character } from '../types'

interface Props {
  character: Character
  onChange: (patch: Partial<Character>) => void
}

export function CharacterLockCard({ character, onChange }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const applyFile = async (file: File | null) => {
    if (!file) {
      return
    }
    if (!file.type.startsWith('image/')) {
      setError('Upload an image (JPG, PNG, WebP).')
      return
    }
    setUploading(true)
    setError(null)
    try {
      const res = await uploadCharacterRef(file)
      onChange({
        referenceImageUrl: res.url,
        faceLocked: true,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    void applyFile(event.dataTransfer.files?.[0] ?? null)
  }

  return (
    <li className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex gap-4">
        <button
          type="button"
          disabled={uploading}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`relative flex h-24 w-24 shrink-0 flex-col items-center justify-center overflow-hidden rounded-2xl border border-dashed text-center transition ${
            dragging
              ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)]'
              : 'border-[var(--color-border)] bg-[var(--color-surface-elevated)] hover:border-[var(--color-accent)]/50'
          }`}
          aria-label={`Upload face lock for ${character.name}`}
        >
          {character.referenceImageUrl ? (
            <img
              src={character.referenceImageUrl}
              alt={character.name}
              className="h-full w-full object-cover"
            />
          ) : (
            <>
              <ImagePlus className="mb-1 h-5 w-5 text-[var(--color-ink-muted)]" />
              <span className="px-1 text-xs font-semibold text-[var(--color-ink-muted)]">
                {uploading ? 'Uploading…' : 'Drop photo'}
              </span>
            </>
          )}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="hidden"
          onChange={(event) => void applyFile(event.target.files?.[0] ?? null)}
        />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <div className="text-base font-bold text-[var(--color-ink)]">{character.name}</div>
              <div className="text-sm font-semibold uppercase tracking-wide text-[var(--color-ink-muted)]">
                {character.role}
              </div>
            </div>
            <span
              className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-bold uppercase tracking-wide ${
                Boolean(character.referenceImageUrl)
                  ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                  : 'bg-[var(--color-surface-elevated)] text-[var(--color-ink-muted)]'
              }`}
            >
              {character.referenceImageUrl ? (
                <>
                  <Lock className="h-3.5 w-3.5" /> Face locked
                </>
              ) : (
                <>
                  <Unlock className="h-3.5 w-3.5" /> No lock
                </>
              )}
            </span>
          </div>
          <p className="meta-text mt-2 leading-relaxed">{character.description}</p>
          {character.referenceImageUrl ? (
            <button
              type="button"
              className="mt-2 inline-flex items-center gap-1 text-sm font-semibold text-[var(--color-ink-muted)] hover:text-[var(--color-accent)]"
              onClick={() =>
                onChange({ referenceImageUrl: null, faceLocked: false })
              }
            >
              <X className="h-3.5 w-3.5" />
              Remove photo
            </button>
          ) : (
            <p className="meta-text mt-2">
              Drag actress / model photo here — same face used in every scene.
            </p>
          )}
          {error ? (
            <p className="mt-1 text-sm font-medium text-[var(--color-danger)]">{error}</p>
          ) : null}
        </div>
      </div>
    </li>
  )
}
