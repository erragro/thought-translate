/**
 * interceptors.ts
 *
 * Auth cookies are sent automatically (withCredentials). The in-memory
 * access token here supplements that for the Authorization header —
 * some future surfaces (WebSocket, external calls) can't read HttpOnly
 * cookies and need the Bearer header instead.
 *
 * On 401, tries /auth/refresh once (using the HttpOnly refresh cookie),
 * then retries the original request. Concurrent 401s share one refresh
 * via the subscriber queue below instead of each firing their own.
 */

import { apiClient } from './client'
import { useAuthStore } from '@/stores/auth.store'

let isRefreshing = false
let refreshSubscribers: Array<(token: string) => void> = []
let _inMemoryAccessToken: string | null = null

function onRefreshed(token: string) {
  refreshSubscribers.forEach((cb) => cb(token))
  refreshSubscribers = []
}

function handleUnauthorized() {
  _inMemoryAccessToken = null
  useAuthStore.getState().logout()
  window.location.href = '/login'
}

async function tryRefresh(): Promise<string | null> {
  try {
    const res = await apiClient.post<{ access_token: string }>('/auth/refresh', {})
    _inMemoryAccessToken = res.data.access_token
    return res.data.access_token
  } catch {
    return null
  }
}

apiClient.interceptors.request.use((config) => {
  if (_inMemoryAccessToken) {
    config.headers['Authorization'] = `Bearer ${_inMemoryAccessToken}`
  }
  return config
})

apiClient.interceptors.response.use(
  (res) => {
    if (res.data?.access_token) {
      _inMemoryAccessToken = res.data.access_token
    }
    return res
  },
  async (error) => {
    const originalRequest = error.config
    if (error.response?.status === 401 && !originalRequest._retry) {
      if (originalRequest.url?.includes('/auth/')) {
        handleUnauthorized()
        return Promise.reject(error)
      }

      originalRequest._retry = true

      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          refreshSubscribers.push((token) => {
            originalRequest.headers['Authorization'] = `Bearer ${token}`
            resolve(apiClient(originalRequest))
          })
          setTimeout(() => reject(error), 10_000)
        })
      }

      isRefreshing = true
      try {
        const newToken = await tryRefresh()
        if (newToken) {
          onRefreshed(newToken)
          originalRequest.headers['Authorization'] = `Bearer ${newToken}`
          return apiClient(originalRequest)
        }
        handleUnauthorized()
        return Promise.reject(error)
      } finally {
        isRefreshing = false
      }
    }
    return Promise.reject(error)
  }
)

export function getAccessToken(): string | null {
  return _inMemoryAccessToken
}

export function setAccessToken(token: string): void {
  _inMemoryAccessToken = token
}
