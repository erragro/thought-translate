import axios from 'axios'

// withCredentials: true — the browser sends the HttpOnly auth cookies
// automatically on every request; this is the whole point of the
// cookie-based token model (no tokens ever touch JS-readable storage).
export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8010',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
})
