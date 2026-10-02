import type {
  DirectorResponse,
  ProductionStage,
  Project,
  User,
} from '../types'

export const MOCK_USER: User = {
  id: 'user-1',
  email: 'creator@example.com',
  name: 'Alex Creator',
}

export const MOCK_PROJECTS: Project[] = [
  {
    id: 'proj-1',
    title: 'Neon Alley Chase',
    status: 'producing',
    durationSeconds: 60,
    createdAt: '2026-09-28T10:15:00.000Z',
    updatedAt: '2026-10-01T14:20:00.000Z',
    language: 'Hindi',
    genre: 'Action',
    idea: 'A courier races through a neon city with a stolen chip.',
    concept:
      'High-energy night chase through rainy neon streets with tight cuts.',
  },
  {
    id: 'proj-2',
    title: 'Quiet Harbor Morning',
    status: 'completed',
    durationSeconds: 90,
    createdAt: '2026-09-12T08:00:00.000Z',
    updatedAt: '2026-09-20T18:40:00.000Z',
    language: 'Hindi',
    genre: 'Documentary',
    idea: 'A documentary-style look at fishermen preparing for dawn.',
  },
  {
    id: 'proj-3',
    title: 'Classroom of Tomorrow',
    status: 'review',
    durationSeconds: 120,
    createdAt: '2026-09-30T16:45:00.000Z',
    updatedAt: '2026-10-02T09:10:00.000Z',
    language: 'Tamil',
    genre: 'Educational',
    idea: 'Students discover an AI tutor that adapts to each learner.',
  },
  {
    id: 'proj-4',
    title: 'Kitchen Remix',
    status: 'failed',
    durationSeconds: 30,
    createdAt: '2026-09-25T12:00:00.000Z',
    updatedAt: '2026-09-26T11:05:00.000Z',
    language: 'Hindi',
    genre: 'Comedy',
    idea: 'A home cook tries viral recipes with chaotic results.',
  },
]

export const MOCK_DIRECTOR_RESPONSE: DirectorResponse = {
  title: 'Signal in the Fog',
  concept:
    'A coastal radio operator receives a mysterious signal during a storm and must decide whether to trust it before the harbor closes.',
  characters: [
    {
      id: 'char-1',
      name: 'Mara Ellison',
      role: 'Protagonist',
      description: 'Night-shift radio operator; calm under pressure, quietly curious.',
    },
    {
      id: 'char-2',
      name: 'Jonah Price',
      role: 'Supporting',
      description: 'Harbor dispatcher who urges caution and wants the night to end quietly.',
    },
    {
      id: 'char-3',
      name: 'The Signal',
      role: 'Antagonist / Mystery',
      description: 'An unidentified voice that knows too much about tomorrow’s weather.',
    },
  ],
  storyStructure: [
    'Setup — introduce the stormy harbor and Mara’s routine',
    'Inciting signal — the first unusual transmission arrives',
    'Rising tension — conflicting advice and rising stakes',
    'Climax — Mara chooses to act on the signal',
    'Resolution — the fog clears and consequences settle',
  ],
  scenes: [
    {
      id: 'scene-1',
      order: 1,
      title: 'Harbor at Dusk',
      description:
        'Wide shot of boats rocking as fog rolls in. Mara enters the radio shack and starts her checklist.',
      durationSeconds: 12,
      status: 'pending',
    },
    {
      id: 'scene-2',
      order: 2,
      title: 'First Crackles',
      description:
        'Static interrupts a routine weather bulletin. Mara leans in and isolates a faint voice.',
      durationSeconds: 15,
      status: 'pending',
    },
    {
      id: 'scene-3',
      order: 3,
      title: 'Dispatcher Doubt',
      description:
        'Jonah calls in, urging her to ignore anomalies. Tension builds as the storm intensifies.',
      durationSeconds: 18,
      status: 'pending',
    },
    {
      id: 'scene-4',
      order: 4,
      title: 'The Choice',
      description:
        'Mara broadcasts a reply against protocol. Lights flicker as the signal strengthens.',
      durationSeconds: 20,
      status: 'pending',
    },
    {
      id: 'scene-5',
      order: 5,
      title: 'Clearing Skies',
      description:
        'Morning light breaks. An unexpected vessel arrives safely — validation and lingering mystery.',
      durationSeconds: 15,
      status: 'pending',
    },
  ],
  estimatedDurationSeconds: 80,
}

export const INITIAL_PRODUCTION_STAGES: ProductionStage[] = [
  { id: 'script', label: 'Script', status: 'pending', progress: 0 },
  { id: 'storyboard', label: 'Storyboard', status: 'pending', progress: 0 },
  { id: 'shots', label: 'Shots', status: 'pending', progress: 0 },
  { id: 'video', label: 'Video', status: 'pending', progress: 0 },
  { id: 'voice', label: 'Voice', status: 'pending', progress: 0 },
  { id: 'sfx_music', label: 'SFX/Music', status: 'pending', progress: 0 },
  { id: 'assembly', label: 'Assembly', status: 'pending', progress: 0 },
  { id: 'final_video', label: 'Final Video', status: 'pending', progress: 0 },
]

export const LANGUAGES = [
  'Hindi',
  'English (India)',
  'Bengali',
  'Gujarati',
  'Kannada',
  'Malayalam',
  'Marathi',
  'Odia',
  'Punjabi',
  'Tamil',
  'Telugu',
] as const

export const GENRES = [
  'Action',
  'Comedy',
  'Documentary',
  'Drama',
  'Educational',
  'Fantasy',
  'Horror',
  'Sci-Fi',
] as const

export const DURATION_PRESETS = [
  { label: '10s', seconds: 10 },
  { label: '30s', seconds: 30 },
  { label: '1m', seconds: 60 },
  { label: '5m', seconds: 300 },
  { label: '10m', seconds: 600 },
  { label: '20m', seconds: 1200 },
  { label: '30m', seconds: 1800 },
] as const
