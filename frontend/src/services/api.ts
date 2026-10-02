import {
  INITIAL_PRODUCTION_STAGES,
  MOCK_DIRECTOR_RESPONSE,
} from '../mock/data'
import type {
  AuthTokenResponse,
  CreateDirectorRequest,
  DirectorResponse,
  LoginCredentials,
  ProductionStage,
  Project,
  ProjectCreatePayload,
  RegisterCredentials,
  Scene,
  SignedAssetUrl,
  User,
} from '../types'
import { apiRequest, setAuthSession } from './client'

export async function register(
  credentials: RegisterCredentials,
): Promise<AuthTokenResponse> {
  const data = await apiRequest<AuthTokenResponse>(
    '/api/auth/register',
    {
      method: 'POST',
      body: JSON.stringify(credentials),
    },
    false,
  )
  setAuthSession(data.accessToken, data.user)
  return data
}

export async function login(
  credentials: LoginCredentials,
): Promise<AuthTokenResponse> {
  const data = await apiRequest<AuthTokenResponse>(
    '/api/auth/login',
    {
      method: 'POST',
      body: JSON.stringify(credentials),
    },
    false,
  )
  setAuthSession(data.accessToken, data.user)
  return data
}

export async function getMe(): Promise<User> {
  return apiRequest<User>('/api/auth/me')
}

export async function getProjects(): Promise<Project[]> {
  return apiRequest<Project[]>('/api/projects')
}

export async function getProject(id: string): Promise<Project> {
  return apiRequest<Project>(`/api/projects/${id}`)
}

export async function getFinalVideoUrl(
  projectId: string,
): Promise<SignedAssetUrl> {
  return apiRequest<SignedAssetUrl>(
    `/api/projects/${projectId}/final-video/url`,
  )
}

export async function createProject(
  payload: ProjectCreatePayload,
): Promise<Project> {
  return apiRequest<Project>('/api/projects', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateProject(
  id: string,
  payload: Partial<ProjectCreatePayload>,
): Promise<Project> {
  return apiRequest<Project>(`/api/projects/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function deleteProject(id: string): Promise<void> {
  await apiRequest<void>(`/api/projects/${id}`, { method: 'DELETE' })
}

export async function generateDirectorResponse(
  request: CreateDirectorRequest,
): Promise<DirectorResponse> {
  return apiRequest<DirectorResponse>('/api/director/generate', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function confirmProduction(
  draft: DirectorResponse,
  meta: Omit<CreateDirectorRequest, 'idea'> & { idea: string },
  existingProjectId?: string,
): Promise<Project> {
  const payload: ProjectCreatePayload = {
    title: draft.title,
    durationSeconds: draft.estimatedDurationSeconds,
    language: meta.language,
    genre: meta.genre,
    idea: meta.idea,
    concept: draft.concept,
    status: 'confirmed',
    directorResponse: draft,
  }

  const project = existingProjectId
    ? await updateProject(existingProjectId, payload)
    : await createProject(payload)

  return project
}

export interface ProductionStatusResponse {
  projectId: string
  status: string
  currentStep: string | null
  pauseReason: string | null
  steps: Array<{ id: string; status: string }>
  scenes: Array<{
    id: string
    order: number
    title: string
    status: string
    durationSeconds: number
  }>
  shots: Array<{
    id: string
    sceneId: string
    order: number
    title: string
    status: string
  }>
  sceneStatus: Record<string, string>
  shotStatus: Record<string, string>
  retryCounts: Record<string, number>
  errors: unknown[]
  running?: boolean
  interrupted?: boolean
}

const STAGE_ID_MAP: Record<string, ProductionStage['id']> = {
  director_planning: 'script',
  scene_planning: 'script',
  shot_planning: 'storyboard',
  asset_planning: 'shots',
  video_generation_planning: 'video',
  audio_planning: 'voice',
  assembly_planning: 'assembly',
  finalization: 'final_video',
}

function mapStatus(value: string): Scene['status'] {
  if (value === 'completed' || value === 'failed' || value === 'running') {
    return value
  }
  if (value === 'paused' || value === 'retrying') {
    return 'running'
  }
  return 'pending'
}

/** Real production status from LangGraph (+ job reconciliation). */
export async function getProductionStatus(
  projectId: string,
): Promise<ProductionStatusResponse> {
  return apiRequest<ProductionStatusResponse>(
    `/api/projects/${projectId}/production/status`,
  )
}

export async function getProjectJobs(projectId: string): Promise<{
  projectId: string
  jobs: Array<Record<string, unknown>>
}> {
  return apiRequest(`/api/projects/${projectId}/jobs`)
}

export async function getProductionState(projectId: string): Promise<{
  stages: ProductionStage[]
  scenes: Scene[]
  raw: ProductionStatusResponse
}> {
  const raw = await getProductionStatus(projectId)
  const byPipeline = new Map<ProductionStage['id'], ProductionStage>()
  for (const template of INITIAL_PRODUCTION_STAGES) {
    byPipeline.set(template.id, { ...template })
  }

  for (const step of raw.steps) {
    const mappedId = STAGE_ID_MAP[step.id]
    if (!mappedId) {
      continue
    }
    const existing = byPipeline.get(mappedId)
    if (!existing) {
      continue
    }
    const status = mapStatus(step.status)
    const progress =
      status === 'completed' ? 100 : status === 'running' ? 55 : status === 'failed' ? 40 : 0
    // Prefer more advanced status if multiple planning steps map to one UI stage.
    const rank = { pending: 0, running: 1, failed: 2, completed: 3 }
    if (rank[status] >= rank[existing.status]) {
      byPipeline.set(mappedId, { ...existing, status, progress })
    }
  }

  if (raw.status === 'completed') {
    for (const [id, stage] of byPipeline) {
      byPipeline.set(id, { ...stage, status: 'completed', progress: 100 })
    }
  }

  const scenes: Scene[] = (raw.scenes.length
    ? raw.scenes
    : MOCK_DIRECTOR_RESPONSE.scenes
  ).map((scene) => ({
    id: scene.id,
    order: scene.order,
    title: scene.title,
    description:
      'description' in scene && typeof scene.description === 'string'
        ? scene.description
        : scene.title,
    durationSeconds: scene.durationSeconds ?? 10,
    status: mapStatus(scene.status),
  }))

  return {
    stages: INITIAL_PRODUCTION_STAGES.map(
      (stage) => byPipeline.get(stage.id) ?? { ...stage },
    ),
    scenes,
    raw,
  }
}

export async function retryFailedItem(
  projectId: string,
  _kind: 'stage' | 'scene',
  _id: string,
): Promise<{ stages: ProductionStage[]; scenes: Scene[] }> {
  await apiRequest(`/api/projects/${projectId}/production/resume`, {
    method: 'POST',
  })
  const state = await getProductionState(projectId)
  return { stages: state.stages, scenes: state.scenes }
}
