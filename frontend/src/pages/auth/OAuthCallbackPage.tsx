/**
 * OAuth tokens arrive as HttpOnly cookies set on the backend redirect —
 * never in the URL. This page just calls /auth/me using that cookie.
 * If the OAuth attempt failed, ?error will be present instead.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth.store'
import { authApi } from '@/api/auth.api'
import { Spinner } from '@/components/ui/Spinner'

export default function OAuthCallbackPage() {
  const navigate = useNavigate()
  const setUser = useAuthStore((s) => s.setUser)
  const [error, setError] = useState('')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const oauthError = params.get('error')

    if (oauthError) {
      setError(`OAuth login failed: ${oauthError}`)
      return
    }

    ;(async () => {
      try {
        const res = await authApi.me()
        setUser(res.data)
        window.history.replaceState({}, '', '/auth/callback')
        navigate('/', { replace: true })
      } catch {
        setError('OAuth login failed. Please try logging in again.')
      }
    })()
  }, [navigate, setUser])

  if (error) {
    return (
      <div className="min-h-screen bg-surface flex items-center justify-center p-4">
        <div className="text-center">
          <p className="text-red-400 font-medium mb-4">{error}</p>
          <a href="/login" className="text-brand-400 hover:text-brand-300 text-sm">
            Back to login
          </a>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <Spinner size="lg" />
        <p className="text-muted text-sm">Completing sign in…</p>
      </div>
    </div>
  )
}
