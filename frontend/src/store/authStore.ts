import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface User {
  user_id: number
  username: string
  is_superuser: boolean
}

interface AuthState {
  token: string | null
  user: User | null
  forcePasswordChange: boolean
  setAuth: (token: string, user: User, forcePasswordChange?: boolean) => void
  clearForcePasswordChange: () => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      forcePasswordChange: false,
      setAuth: (token: string, user: User, forcePasswordChange = false) => {
        localStorage.setItem('token', token)
        set({ token, user, forcePasswordChange })
      },
      clearForcePasswordChange: () => {
        set({ forcePasswordChange: false })
      },
      logout: () => {
        localStorage.removeItem('token')
        set({ token: null, user: null, forcePasswordChange: false })
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        token: state.token,
        user: state.user,
        forcePasswordChange: state.forcePasswordChange,
      }),
    }
  )
)
