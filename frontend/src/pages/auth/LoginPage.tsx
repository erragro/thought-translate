import { useState } from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth.store'
import { authApi } from '@/api/auth.api'
import { setAccessToken } from '@/api/interceptors'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8010'

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const setUser = useAuthStore((s) => s.setUser)
  const from = (location.state as { from?: { pathname: string } })?.from?.pathname ?? '/'

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !password) {
      setError('Email and password are required')
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = await authApi.login(email.trim(), password)
      const { access_token, user } = res.data
      setAccessToken(access_token)
      setUser(user)
      navigate(from, { replace: true })
    } catch (err: unknown) {
      const axiosError = err as { response?: { status: number; data?: { detail?: string } } }
      if (axiosError.response?.status === 401) {
        setError('Invalid email or password.')
      } else if (axiosError.response?.status === 403) {
        setError('Account is disabled. Contact your administrator.')
      } else if (axiosError.response?.status === 429) {
        setError('Too many failed attempts. Try again in 15 minutes.')
      } else {
        setError(axiosError.response?.data?.detail ?? 'Login failed. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleOAuth = (provider: 'github' | 'google' | 'microsoft') => {
    window.location.href = `${API_URL}/auth/oauth/${provider}`
  }

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 rounded-xl bg-brand-600 flex items-center justify-center mb-4 shadow-lg shadow-brand-600/20">
            <span className="text-white text-2xl font-bold">TT</span>
          </div>
          <h1 className="text-2xl font-bold text-foreground">Thought Translate</h1>
          <p className="text-muted text-sm mt-1">Translation that mirrors thought, not words</p>
        </div>

        <div className="bg-surface-card border border-surface-border rounded-xl p-6 shadow-xl">
          <h2 className="text-base font-semibold text-foreground mb-5">Sign in to your account</h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Email"
              type="email"
              id="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              autoFocus
            />
            <Input
              label="Password"
              type="password"
              id="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              error={error}
              autoComplete="current-password"
            />

            <Button type="submit" className="w-full mt-2" loading={loading} disabled={!email.trim() || !password}>
              Sign In
            </Button>
          </form>

          <div className="relative my-5">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-surface-border" />
            </div>
            <div className="relative flex justify-center text-xs text-subtle">
              <span className="bg-surface-card px-2">or continue with</span>
            </div>
          </div>

          <div className="space-y-2">
            <button
              type="button"
              onClick={() => handleOAuth('github')}
              className="flex items-center justify-center gap-2.5 w-full px-4 py-2 text-sm text-foreground border border-surface-border rounded-lg hover:bg-surface transition-colors"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z" />
              </svg>
              GitHub
            </button>

            <button
              type="button"
              onClick={() => handleOAuth('google')}
              className="flex items-center justify-center gap-2.5 w-full px-4 py-2 text-sm text-foreground border border-surface-border rounded-lg hover:bg-surface transition-colors"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
              </svg>
              Google
            </button>

            <button
              type="button"
              onClick={() => handleOAuth('microsoft')}
              className="flex items-center justify-center gap-2.5 w-full px-4 py-2 text-sm text-foreground border border-surface-border rounded-lg hover:bg-surface transition-colors"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24">
                <path fill="#f25022" d="M1 1h10v10H1z" />
                <path fill="#00a4ef" d="M13 1h10v10H13z" />
                <path fill="#7fba00" d="M1 13h10v10H1z" />
                <path fill="#ffb900" d="M13 13h10v10H13z" />
              </svg>
              Microsoft
            </button>
          </div>

          <p className="text-center text-sm text-muted mt-5">
            Don't have an account?{' '}
            <Link to="/signup" className="text-brand-400 hover:text-brand-300 font-medium">
              Sign up
            </Link>
          </p>
        </div>

        <p className="text-center text-xs text-subtle mt-6">Thought Translate</p>
      </div>
    </div>
  )
}
