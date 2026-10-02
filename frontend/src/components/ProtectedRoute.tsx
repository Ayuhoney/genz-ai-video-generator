import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuthContext } from '../hooks/useAuthContext'
import { Spinner } from '../components/States'

export function ProtectedRoute() {
  const { isAuthenticated, isLoading } = useAuthContext()
  const location = useLocation()

  if (isLoading) {
    return <Spinner label="Checking session…" />
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <Outlet />
}
