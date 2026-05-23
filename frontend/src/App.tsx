// src/App.tsx
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// Pages (we will build these next)
const ProjectsPage = () => <div className="p-8 text-white">Projects — coming soon</div>
const ProjectPage  = () => <div className="p-8 text-white">Project detail — coming soon</div>
const JobPage      = () => <div className="p-8 text-white">Job / Script editor — coming soon</div>
const NotFoundPage = () => <div className="p-8 text-white">404 — Page not found</div>

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 5000 },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="min-h-screen bg-gray-950 text-gray-100">
          <Routes>
            <Route path="/"                                     element={<Navigate to="/projects" replace />} />
            <Route path="/projects"                             element={<ProjectsPage />} />
            <Route path="/projects/:projectId"                  element={<ProjectPage />} />
            <Route path="/projects/:projectId/jobs/:jobId"      element={<JobPage />} />
            <Route path="*"                                     element={<NotFoundPage />} />
          </Routes>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}