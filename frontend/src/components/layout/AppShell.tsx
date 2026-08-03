import { useNavigate } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { useAuthStore } from '@/stores/auth.store'
import { authApi } from '@/api/auth.api'
import { Button } from '@/components/ui/Button'

export function AppShell({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()

  const handleLogout = async () => {
    try {
      await authApi.logout()
    } catch {
      // cookie may already be gone — clear local state regardless
    }
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen flex bg-surface p-3 gap-3">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 gap-3">
        <header className="h-14 rounded-2xl border border-surface-border bg-surface-card shadow-sm flex items-center justify-end px-6 gap-4 shrink-0">
          <span className="text-sm text-muted">{user?.full_name}</span>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            Log out
          </Button>
        </header>
        <main className="flex-1 min-w-0 overflow-auto rounded-2xl border border-surface-border bg-surface shadow-sm">
          {children}
        </main>
      </div>
    </div>
  )
}
