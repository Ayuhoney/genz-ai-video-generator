import { RefreshCw, Sparkles, Wand2 } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '../components/Button'
import { CharacterLockCard } from '../components/CharacterLockCard'
import { ConfirmModal } from '../components/ConfirmModal'
import { Input, Select, Textarea } from '../components/FormFields'
import { ErrorState, Skeleton } from '../components/States'
import {
  DURATION_PRESETS,
  GENRES,
  LANGUAGES,
} from '../mock/data'
import {
  confirmProduction,
  generateDirectorResponse,
} from '../services/api'
import { formatDuration } from '../services/utils'
import type { Character, DirectorResponse } from '../types'

interface FormState {
  idea: string
  durationSeconds: number
  language: string
  genre: string
  instructions: string
}

interface FormErrors {
  idea?: string
}

const MIN_DURATION = 10
const MAX_DURATION = 1800

export function CreatePage() {
  const navigate = useNavigate()
  const [form, setForm] = useState<FormState>({
    idea: '',
    durationSeconds: 60,
    language: 'Hindi',
    genre: 'Drama',
    instructions: '',
  })
  const [errors, setErrors] = useState<FormErrors>({})
  const [director, setDirector] = useState<DirectorResponse | null>(null)
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)

  const estimatedLabel = useMemo(
    () => formatDuration(form.durationSeconds),
    [form.durationSeconds],
  )

  const validate = () => {
    const next: FormErrors = {}
    if (!form.idea.trim()) {
      next.idea = 'Describe your video idea to continue.'
    } else if (form.idea.trim().length < 12) {
      next.idea = 'Add a bit more detail (at least 12 characters).'
    }
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const runGenerate = async () => {
    if (!validate()) {
      return
    }
    setGenerating(true)
    setGenerateError(null)
    try {
      const response = await generateDirectorResponse(form)
      setDirector(response)
    } catch (error) {
      setGenerateError(
        error instanceof Error ? error.message : 'Failed to generate.',
      )
    } finally {
      setGenerating(false)
    }
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    await runGenerate()
  }

  const updateScene = (
    sceneId: string,
    patch: Partial<DirectorResponse['scenes'][number]>,
  ) => {
    setDirector((prev) => {
      if (!prev) {
        return prev
      }
      const scenes = prev.scenes.map((scene) =>
        scene.id === sceneId ? { ...scene, ...patch } : scene,
      )
      const estimatedDurationSeconds = scenes.reduce(
        (sum, scene) => sum + scene.durationSeconds,
        0,
      )
      return { ...prev, scenes, estimatedDurationSeconds }
    })
  }

  const updateCharacter = (characterId: string, patch: Partial<Character>) => {
    setDirector((prev) => {
      if (!prev) {
        return prev
      }
      return {
        ...prev,
        characters: prev.characters.map((character) =>
          character.id === characterId ? { ...character, ...patch } : character,
        ),
      }
    })
  }

  const onConfirmProduction = async () => {
    if (!director) {
      return
    }
    setConfirming(true)
    setConfirmError(null)
    try {
      const project = await confirmProduction(director, form)
      setConfirmOpen(false)
      navigate(`/project/${project.id}`)
    } catch (error) {
      setConfirmError(
        error instanceof Error ? error.message : 'Could not start production.',
      )
    } finally {
      setConfirming(false)
    }
  }

  return (
    <div className="space-y-8">
      <div className="anim-rise">
        <p className="gc-eyebrow">Cine AI director</p>
        <h1 className="page-title mt-2">Create Video</h1>
        <p className="body-muted mt-3 max-w-xl">
          Share an idea. The AI Director drafts concept, characters, and scenes
          for a quick review. Total length can be minutes — each fal video clip
          is up to 15 seconds, then clips are stitched together.
        </p>
      </div>

      <form
        onSubmit={onSubmit}
        className="anim-rise-delay space-y-5 rounded-[1.35rem] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-5 sm:p-7"
      >
        <Textarea
          label="Video idea"
          name="idea"
          value={form.idea}
          onChange={(event) =>
            setForm((prev) => ({ ...prev, idea: event.target.value }))
          }
          error={errors.idea}
          placeholder="A night courier races through a neon city with a stolen chip…"
        />

        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <span className="label-text">Duration</span>
            <span className="text-base font-semibold text-[var(--color-ink)]">
              {estimatedLabel}
            </span>
          </div>
          <input
            type="range"
            min={MIN_DURATION}
            max={MAX_DURATION}
            step={10}
            value={form.durationSeconds}
            onChange={(event) =>
              setForm((prev) => ({
                ...prev,
                durationSeconds: Number(event.target.value),
              }))
            }
            className="w-full accent-[var(--color-accent)]"
            aria-label="Duration in seconds"
          />
          <div className="flex flex-wrap gap-2">
            {DURATION_PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() =>
                  setForm((prev) => ({
                    ...prev,
                    durationSeconds: preset.seconds,
                  }))
                }
                className={`rounded-xl px-4 py-2 text-sm font-semibold transition ${
                  form.durationSeconds === preset.seconds
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'border border-[var(--color-border)] text-[var(--color-ink-muted)] hover:border-[var(--color-ink)]/25 hover:text-[var(--color-ink)]'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Language"
            name="language"
            options={LANGUAGES}
            value={form.language}
            onChange={(event) =>
              setForm((prev) => ({ ...prev, language: event.target.value }))
            }
          />
          <Select
            label="Genre / style"
            name="genre"
            options={GENRES}
            value={form.genre}
            onChange={(event) =>
              setForm((prev) => ({ ...prev, genre: event.target.value }))
            }
          />
        </div>

        <Textarea
          label="Additional instructions"
          name="instructions"
          value={form.instructions}
          onChange={(event) =>
            setForm((prev) => ({ ...prev, instructions: event.target.value }))
          }
          placeholder="Tone, pacing, must-include moments, brand notes…"
        />

        <div className="flex flex-col gap-2 sm:flex-row">
          <Button type="submit" loading={generating} disabled={generating}>
            <Wand2 className="h-4 w-4" />
            Generate with AI Director
          </Button>
          {director ? (
            <Button
              type="button"
              variant="secondary"
              loading={generating}
              onClick={() => void runGenerate()}
            >
              <RefreshCw className="h-4 w-4" />
              Regenerate
            </Button>
          ) : null}
        </div>
      </form>

      {generating ? (
        <div className="space-y-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-5">
          <div className="flex items-center gap-2 text-sm text-[var(--color-ink-muted)]">
            <Sparkles className="h-4 w-4 animate-pulse text-[var(--color-accent)]" />
            AI Director is drafting your production plan…
          </div>
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : null}

      {!generating && generateError ? (
        <ErrorState
          message={generateError}
          onRetry={() => void runGenerate()}
        />
      ) : null}

      {!generating && director ? (
        <section className="anim-fade space-y-8 rounded-[1.35rem] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-5 sm:p-7">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="gc-eyebrow">AI Director</p>
              <h2 className="section-title mt-1">Production plan</h2>
            </div>
            <p className="text-base text-[var(--color-ink-muted)]">
              Estimated total:{' '}
              <span className="font-semibold text-[var(--color-ink)]">
                {formatDuration(director.estimatedDurationSeconds)}
              </span>
            </p>
          </div>

          <Input
            label="Title"
            name="director-title"
            value={director.title}
            onChange={(event) =>
              setDirector((prev) =>
                prev ? { ...prev, title: event.target.value } : prev,
              )
            }
          />

          <Textarea
            label="Concept"
            name="director-concept"
            value={director.concept}
            onChange={(event) =>
              setDirector((prev) =>
                prev ? { ...prev, concept: event.target.value } : prev,
              )
            }
          />

          <div>
            <h3 className="section-title">1 · Director script</h3>
            <p className="meta-text mt-1">
              Full screenplay from the AI Director — edit before production.
            </p>
            <textarea
              className="mt-3 min-h-48 w-full resize-y rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 font-mono text-sm leading-relaxed outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent)]/15 sm:text-[0.9375rem]"
              value={director.script || ''}
              onChange={(event) =>
                setDirector((prev) =>
                  prev ? { ...prev, script: event.target.value } : prev,
                )
              }
              aria-label="Director script"
            />
          </div>

          <div>
            <h3 className="section-title">2 · Character face lock</h3>
            <p className="meta-text mt-1">
              Drag & drop actress / model photos so the same face is used in every
              scene.
            </p>
            <ul className="mt-3 grid gap-3 lg:grid-cols-2">
              {director.characters.map((character) => (
                <CharacterLockCard
                  key={character.id}
                  character={character}
                  onChange={(patch) => updateCharacter(character.id, patch)}
                />
              ))}
            </ul>
          </div>

          <div>
            <h3 className="section-title mb-2">Story structure</h3>
            <ol className="space-y-2">
              {director.storyStructure.map((beat, index) => (
                <li
                  key={`${beat}-${index}`}
                  className="rounded-xl bg-[var(--color-accent-soft)]/60 px-3.5 py-2.5 text-base"
                >
                  <span className="mr-2 font-semibold text-[var(--color-accent)]">
                    {index + 1}.
                  </span>
                  {beat}
                </li>
              ))}
            </ol>
          </div>

          <div>
            <h3 className="section-title">3 · Scenes, shots & voice</h3>
            <p className="meta-text mt-1">
              Each scene shows planned shots and voice lines that will be generated.
            </p>
            <ul className="mt-3 space-y-4">
              {director.scenes.map((scene) => (
                <li
                  key={scene.id}
                  className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
                >
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-bold uppercase tracking-wide text-[var(--color-ink-muted)]">
                      Scene {scene.order}
                    </span>
                    <span className="text-sm font-semibold text-[var(--color-ink-muted)]">
                      {formatDuration(scene.durationSeconds)}
                    </span>
                  </div>
                  <input
                    className="mb-2 w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-3 text-base font-semibold outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent)]/15"
                    value={scene.title}
                    onChange={(event) =>
                      updateScene(scene.id, { title: event.target.value })
                    }
                    aria-label={`Scene ${scene.order} title`}
                  />
                  <textarea
                    className="w-full resize-y rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-3 text-base outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent)]/15"
                    rows={2}
                    value={scene.description}
                    onChange={(event) =>
                      updateScene(scene.id, {
                        description: event.target.value,
                      })
                    }
                    aria-label={`Scene ${scene.order} description`}
                  />

                  {scene.sfxNotes ? (
                    <p className="meta-text mt-2">
                      <span className="font-semibold text-[var(--color-ink)]">SFX:</span>{' '}
                      {scene.sfxNotes}
                    </p>
                  ) : null}

                  {(scene.shots?.length ?? 0) > 0 ? (
                    <div className="mt-3">
                      <p className="mb-1.5 text-sm font-bold uppercase tracking-wide text-[var(--color-accent)]">
                        Shots
                      </p>
                      <ul className="space-y-1.5">
                        {scene.shots?.map((shot) => (
                          <li
                            key={shot.id}
                            className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3.5 py-2.5 text-base"
                          >
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <span className="font-semibold">
                                {shot.order}. {shot.title}
                              </span>
                              <span className="meta-text">
                                {formatDuration(shot.durationSeconds)}
                                {shot.camera ? ` · ${shot.camera}` : ''}
                              </span>
                            </div>
                            <p className="meta-text mt-0.5">
                              {shot.description}
                            </p>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}

                  {(scene.voiceOver?.length ?? 0) > 0 ? (
                    <div className="mt-3">
                      <p className="mb-1.5 text-sm font-bold uppercase tracking-wide text-[var(--color-accent)]">
                        Voice / dialogue
                      </p>
                      <ul className="space-y-1.5">
                        {scene.voiceOver?.map((line) => (
                          <li
                            key={line.id}
                            className="rounded-xl bg-[var(--color-accent-soft)]/50 px-3.5 py-2.5 text-base"
                          >
                            <span className="font-semibold text-[var(--color-accent)]">
                              {line.characterName}:
                            </span>{' '}
                            <span className="text-[var(--color-ink)]">“{line.text}”</span>
                            <span className="meta-text ml-2">
                              (~{line.estimatedSeconds}s)
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>

          <div className="flex flex-col gap-2 border-t border-[var(--color-border)] pt-4 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="secondary"
              loading={generating}
              onClick={() => void runGenerate()}
            >
              <RefreshCw className="h-4 w-4" />
              Regenerate
            </Button>
            <Button type="button" onClick={() => setConfirmOpen(true)}>
              Confirm & Start Production
            </Button>
          </div>
        </section>
      ) : null}

      <ConfirmModal
        open={confirmOpen}
        title="Start production?"
        description="This locks the script, face locks, shots, and voice plan — then starts generation."
        confirmLabel="Start production"
        loading={confirming}
        onClose={() => {
          if (!confirming) {
            setConfirmOpen(false)
            setConfirmError(null)
          }
        }}
        onConfirm={() => void onConfirmProduction()}
      >
        {confirmError ? (
          <p className="rounded-lg bg-[var(--color-danger-soft)] px-3 py-2 text-sm text-[var(--color-danger)]">
            {confirmError}
          </p>
        ) : (
          <p className="text-sm text-[var(--color-ink-muted)]">
            Title: <strong className="text-[var(--color-ink)]">{director?.title}</strong>
            <br />
            Duration:{' '}
            <strong className="text-[var(--color-ink)]">
              {director
                ? formatDuration(director.estimatedDurationSeconds)
                : '—'}
            </strong>
            <br />
            Face locks:{' '}
            <strong className="text-[var(--color-ink)]">
              {director?.characters.filter((c) => Boolean(c.referenceImageUrl))
                .length ?? 0}
            </strong>
          </p>
        )}
      </ConfirmModal>
    </div>
  )
}
