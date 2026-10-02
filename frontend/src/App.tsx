import { Navigate, Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from './components/ProtectedRoute'
import { AuthProvider } from './context/AuthProvider'
import { AppLayout } from './layouts/AppLayout'
import { CreatePage } from './pages/CreatePage'
import { DashboardPage } from './pages/DashboardPage'
import { LoginPage, RegisterPage } from './pages/LoginPage'
import { ProjectPage } from './pages/ProjectPage'

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppLayout />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/create" element={<CreatePage />} />
            <Route path="/project/:id" element={<ProjectPage />} />
          </Route>
        </Route>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AuthProvider>
  )
}
