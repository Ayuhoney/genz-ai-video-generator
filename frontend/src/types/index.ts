export type ProjectStatus =
  | 'draft'
  | 'review'
  | 'confirmed'
  | 'producing'
  | 'completed'
  | 'failed'

export interface User {
  id: string
  email: string
  name: string
}

export interface Project {
  id: string
  title: string
  status: ProjectStatus
  durationSeconds: number
  createdAt: string
  updatedAt: string
  language: string
  genre: string
  idea?: string | null
  concept?: string | null
  directorResponse?: DirectorResponse | Record<string, unknown> | null
  finalVideoAssetId?: string | null
}

export interface SignedAssetUrl {
  projectId: string
  assetId: string
  url: string
  expiresIn: number
  r2Key: string
  mime?: string | null
}

export interface Character {
  id: string
  name: string
  description: string
  role: string
}

export type ProductionStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface Scene {
  id: string
  order: number
  title: string
  description: string
  durationSeconds: number
  status: ProductionStatus
}

export interface DirectorResponse {
  title: string
  concept: string
  characters: Character[]
  storyStructure: string[]
  scenes: Scene[]
  estimatedDurationSeconds: number
}

export type ProductionStageId =
  | 'script'
  | 'storyboard'
  | 'shots'
  | 'video'
  | 'voice'
  | 'sfx_music'
  | 'assembly'
  | 'final_video'

export interface ProductionStage {
  id: ProductionStageId
  label: string
  status: ProductionStatus
  progress: number
}

export interface CreateDirectorRequest {
  idea: string
  durationSeconds: number
  language: string
  genre: string
  instructions: string
}

export interface LoginCredentials {
  email: string
  password: string
}

export interface RegisterCredentials {
  email: string
  password: string
  name: string
}

export interface AuthTokenResponse {
  accessToken: string
  tokenType: string
  user: User
}

export interface ProjectCreatePayload {
  title: string
  durationSeconds: number
  language: string
  genre: string
  idea?: string
  concept?: string
  status?: ProjectStatus
  directorResponse?: DirectorResponse | Record<string, unknown>
}

export interface ProjectUpdatePayload {
  title?: string
  durationSeconds?: number
  language?: string
  genre?: string
  idea?: string
  concept?: string
  status?: ProjectStatus
  directorResponse?: DirectorResponse | Record<string, unknown>
}
