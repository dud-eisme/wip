// In production these reflect real health checks against Model 1's and
// Model 2's actual APIs (e.g. a periodic GET /health call per adapter).

export const mockAdapters = [
  {
    id: 'adapter-model1',
    name: 'Registry Adapter',
    sourceModel: 'Model 1 — CCTV Registry',
    endpoint: 'http://localhost:8000/api/v1',
    status: 'connected',
    lastSync: new Date(Date.now() - 12000).toISOString(),
    recordsSynced: 5,
  },
  {
    id: 'adapter-model2',
    name: 'Viewer Adapter',
    sourceModel: 'Model 2 — Unified Viewer',
    endpoint: 'http://localhost:8001/api/v1',
    status: 'connected',
    lastSync: new Date(Date.now() - 8000).toISOString(),
    recordsSynced: 15,
  },
]
