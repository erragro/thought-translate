import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth.store'
import { authApi } from '@/api/auth.api'
import { setAccessToken } from '@/api/interceptors'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'

export default function SignupPage() {
  const navigate = useNavigate()
  const setUser = useAuthStore((s) => s.setUser)

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!fullName.trim() || !email.trim() || !password) {
      setError('All fields are required')
      return
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match')
      return
    }

    setLoading(true)
    try {
      const res = await authApi.signup(email.trim(), password, fullName.trim())
      const { access_token, user } = res.data
      setAccessToken(access_token)
      setUser(user)
      navigate('/', { replace: true })
    } catch (err: unknown) {
      const axiosError = err as { response?: { status: number; data?: { detail?: string | { msg: string }[] } } }
      const detail = axiosError.response?.data?.detail
      if (axiosError.response?.status === 409) {
        setError('An account with this email already exists.')
      } else if (Array.isArray(detail)) {
        setError(detail[0]?.msg ?? 'Sign up failed. Please try again.')
      } else {
        setError(detail ?? 'Sign up failed. Please try again.')
      }
    } finally {
      setLoading(false)
    }
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
          <h2 className="text-base font-semibold text-foreground mb-5">Create your account</h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Full Name"
              type="text"
              id="fullName"
              placeholder="Jane Smith"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              autoComplete="name"
              autoFocus
            />
            <Input
              label="Email"
              type="email"
              id="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
            <Input
              label="Password"
              type="password"
              id="password"
              placeholder="Min. 12 chars, upper + digit + symbol"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
            <Input
              label="Confirm Password"
              type="password"
              id="confirmPassword"
              placeholder="Re-enter password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              error={error}
              autoComplete="new-password"
            />

            <Button
              type="submit"
              className="w-full mt-2"
              loading={loading}
              disabled={!fullName.trim() || !email.trim() || !password || !confirmPassword}
            >
              Create Account
            </Button>
          </form>

          <p className="text-center text-sm text-muted mt-5">
            Already have an account?{' '}
            <Link to="/login" className="text-brand-400 hover:text-brand-300 font-medium">
              Sign in
            </Link>
          </p>
        </div>

        <p className="text-center text-xs text-subtle mt-6">Thought Translate</p>
      </div>
    </div>
  )
}
