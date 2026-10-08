import 'pretendard/dist/web/variable/pretendardvariable.css'
import './styles.css'
import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { App } from './App'
import { ToastProvider } from './components/ui'
import { AuthProvider } from './lib/auth'
import { I18nProvider } from './lib/i18n'
import { queryClient } from './lib/query'

try {
  const theme = localStorage.getItem('anx.theme')
  if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme)
} catch {
  /* 저장소 없음 */
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <AuthProvider>
          <ToastProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </ToastProvider>
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
)
