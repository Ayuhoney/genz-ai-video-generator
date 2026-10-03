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
  referenceImageUrl?: string | null
  faceLocked?: boolean
}

export interface ShotPlan {
  id: string
  order: number
  title: string
  description: string
  durationSeconds: number
  camera?: string
}

export interface VoiceLine {
  id: string
  characterId?: string | null
  characterName: string
  text: string
  estimatedSeconds: number
}

export type ProductionStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface Scene {
  id: string
  order: number
  title: string
  description: string
  durationSeconds: number
  status: ProductionStatus
  shots?: ShotPlan[]
  voiceOver?: VoiceLine[]
  sfxNotes?: string
}

export interface DirectorResponse {
  title: string
  concept: string
  script: string
  characters: Character[]
  storyStructure: string[]
  scenes: Scene[]
  estimatedDurationSeconds: number
}

export interface FalCredits {
  available: boolean
  balance: number | null
  currency: string
  username: string | null
  message: string | null
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

export type ProductionIssueCode =
  | 'content_policy'
  | 'needs_new_image'
  | 'retryable'
  | 'failed'
  | 'stuck'

export type ProductionIssueAction =
  | 'regenerate_still'
  | 'retry'
  | 'auto_fix'
  | 'guided'
  | 'resume_missing'

export type FixShotMode = 'auto_fix' | 'guided' | 'retry' | 'new_still'

export interface ProductionIssue {
  shotId?: string | null
  sceneId?: string | null
  jobType: string
  jobStatus: string
  code: ProductionIssueCode
  action: ProductionIssueAction
  message: string
  error?: string | null
  stuckJobCount?: number
  stuckShotIds?: string[]
}

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
