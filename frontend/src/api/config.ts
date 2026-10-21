export const DEFAULT_API_BASE = 'http://localhost:8765'

function normalizeBase(raw: string | undefined): string {
  const value = (raw ?? '').trim()
  if (!value) return DEFAULT_API_BASE
  return value.replace(/\/+$/, '')
}

export const API_BASE: string = normalizeBase(import.meta.env.VITE_API_BASE)

export function apiUrl(path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE}${suffix}`
}

export function sseUrl(path: string): string {
  return apiUrl(path)
}
