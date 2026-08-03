/**
 * auth.store.ts
 *
 * Tokens live in HttpOnly cookies set by the backend — this store only
 * ever holds the user profile. Nothing token-shaped touches localStorage,
 * so there's no XSS-readable credential to steal.
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface UserPermissions {
  view: boolean
  edit: boolean
  admin: boolean
}

export interface User {
  id: number
  email: string
  full_name: string
  avatar_url?: string | null
  is_super_admin: boolean
  permissions: Record<string, UserPermissions>
}

interface AuthStore {
  user: User | null
  setUser: (user: User) => void
  logout: () => void
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set) => ({
      user: null,
      setUser: (user) => set({ user }),
      logout: () => set({ user: null }),
    }),
    {
      name: 'tt_auth',
      partialize: (state) => ({ user: state.user }),
    }
  )
)
