import React from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Layout } from 'antd'
import AppLayout from './components/Layout/AppLayout'
import ChatPage from './pages/ChatPage'
import HistoryPage from './pages/HistoryPage'
import SettingsPage from './pages/SettingsPage'
import AboutPage from './pages/AboutPage'
import DashboardPage from './pages/DashboardPage'
import IssuesPage from './pages/IssuesPage'
import AlertsPage from './pages/AlertsPage'
import ClMonitoringPage from './pages/ClMonitoringPage'
import ScheduledTasksPage from './pages/ScheduledTasksPage'
import SkillsConfigPage from './pages/SkillsConfigPage'
import LoginPage from './pages/LoginPage'
import ChangePasswordPage from './pages/ChangePasswordPage'
import UserManagementPage from './pages/UserManagementPage'
import { useAuthStore } from './store/authStore'

const { Content } = Layout

function RequireAuth({ children }: { children: React.ReactElement }) {
  const token = useAuthStore((s) => s.token)
  const forcePasswordChange = useAuthStore((s) => s.forcePasswordChange)
  const location = useLocation()

  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  // If user needs to change password, always redirect to change-password page
  // This handles stale persisted state from previous login
  if (forcePasswordChange && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />
  }
  return children
}

function LoginGuard({ children }: { children: React.ReactElement }) {
  const token = useAuthStore((s) => s.token)
  const forcePasswordChange = useAuthStore((s) => s.forcePasswordChange)

  if (token) {
    // If user needs to change password, redirect there instead of home
    if (forcePasswordChange) {
      return <Navigate to="/change-password" replace />
    }
    return <Navigate to="/" replace />
  }
  return children
}

function AdminOnly({ children }: { children: React.ReactElement }) {
  const user = useAuthStore((s) => s.user)
  if (!user?.is_superuser) {
    return <Navigate to="/" replace />
  }
  return children
}

const App: React.FC = () => {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginGuard><LoginPage /></LoginGuard>} />
        <Route path="/change-password" element={<RequireAuth><ChangePasswordPage /></RequireAuth>} />
        <Route path="/admin/users" element={
          <RequireAuth>
            <AdminOnly>
              <AppLayout>
                <Content style={{ height: '100%' }}>
                  <UserManagementPage />
                </Content>
              </AppLayout>
            </AdminOnly>
          </RequireAuth>
        } />
        <Route
          path="/*"
          element={
            <RequireAuth>
              <AppLayout>
                <Content style={{ height: '100%' }}>
                  <Routes>
                    <Route path="/" element={<ChatPage />} />
                    <Route path="/chat" element={<ChatPage />} />
                    <Route path="/history" element={<HistoryPage />} />
                    <Route path="/dashboard" element={<DashboardPage />} />
                    <Route path="/issues" element={<IssuesPage />} />
                    <Route path="/alerts" element={<AlertsPage />} />
                    <Route path="/cl-monitoring" element={<ClMonitoringPage />} />
                    <Route path="/scheduled-tasks" element={<ScheduledTasksPage />} />
                    <Route path="/settings" element={<SettingsPage />} />
                    <Route path="/skills-config" element={<SkillsConfigPage />} />
                    <Route path="/about" element={<AboutPage />} />
                    <Route path="*" element={<ChatPage />} />
                  </Routes>
                </Content>
              </AppLayout>
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}

export default App
