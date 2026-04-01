// API client for backend communication
import axios from 'axios'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor
api.interceptors.request.use(
  (config) => {
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// Response interceptor
api.interceptors.response.use(
  (response) => {
    return response.data
  },
  (error) => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

// Topic API
export const topicApi = {
  analyze: (topic) => api.post('/topics/analyze', { topic }),
}

// Agent API
export const agentApi = {
  generate: (data) => api.post('/agents/generate', data),
}

// Review API
export const reviewApi = {
  create: (data) => api.post('/reviews/create', data),
  get: (taskId) => api.get(`/reviews/${taskId}`),
  getResult: (taskId) => api.get(`/reviews/${taskId}/result`),
  list: () => api.get('/reviews/'),
}

// Export API
export const exportApi = {
  // 导出所有文献为 xlsx
  downloadPapersXlsx: (taskId) => {
    window.open(`/api/v1/exports/${taskId}/papers.xlsx`, '_blank')
  },
  // 导出选中文献为 xlsx
  downloadSelectedXlsx: (taskId) => {
    window.open(`/api/v1/exports/${taskId}/selected.xlsx`, '_blank')
  },
  // 导出 BibTeX 格式
  downloadBibtex: (taskId) => {
    window.open(`/api/v1/exports/${taskId}/papers.bib`, '_blank')
  },
  // 导出 CSV 格式
  downloadCsv: (taskId) => {
    window.open(`/api/v1/exports/${taskId}/papers.csv`, '_blank')
  },
}

export default api
