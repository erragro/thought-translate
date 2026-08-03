import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthGuard } from '@/components/layout/AuthGuard'
import { AppShell } from '@/components/layout/AppShell'
import LoginPage from '@/pages/auth/LoginPage'
import SignupPage from '@/pages/auth/SignupPage'
import OAuthCallbackPage from '@/pages/auth/OAuthCallbackPage'
import TranslatePage from '@/pages/translate/TranslatePage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/auth/callback" element={<OAuthCallbackPage />} />
        <Route path="/" element={<Navigate to="/translate" replace />} />
        <Route
          path="/translate"
          element={
            <AuthGuard>
              <AppShell>
                <TranslatePage />
              </AppShell>
            </AuthGuard>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
