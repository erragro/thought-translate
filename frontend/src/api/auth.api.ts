import { apiClient } from './client'
import type { User } from '@/stores/auth.store'

interface TokenResponse {
  access_token: string
  refresh_token: string
  user: User
}

export const authApi = {
  login: (email: string, password: string) =>
    apiClient.post<TokenResponse>('/auth/login', { email, password }),

  signup: (email: string, password: string, full_name: string) =>
    apiClient.post<TokenResponse>('/auth/signup', { email, password, full_name }),

  refresh: () => apiClient.post<{ access_token: string; refresh_token: string }>('/auth/refresh', {}),

  logout: () => apiClient.post('/auth/logout', {}),

  me: () => apiClient.get<User>('/auth/me'),
}
