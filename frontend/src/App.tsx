import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Profile } from '@/pages/Profile'
import { Chat } from '@/pages/Chat'

// The SPA is mounted under /app (Vite base + FastAPI fallback). React Router's
// basename keeps client routes relative to that prefix. As more pages port,
// add them here; unknown routes fall back to the profile pilot for now.
export default function App() {
  return (
    <BrowserRouter basename="/app">
      <Routes>
        <Route path="/profile" element={<Profile />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="*" element={<Navigate to="/profile" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
