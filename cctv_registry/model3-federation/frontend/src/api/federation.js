// Same USE_MOCK seam pattern as Models 1 and 2. The real difference here:
// Model 3's backend doesn't own its own source data — it's a middleware
// layer whose whole job is querying Model 1's and Model 2's real APIs,
// correlating the results, and exposing one unified endpoint. See the
// adapter pattern described in the backend README for the actual join
// logic this frontend expects to consume.

import { mockAdapters } from '../data/mockAdapters'
import { mockFederatedEvents } from '../data/mockFederatedEvents'

const USE_MOCK = true
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8002/api/v1'

export async function getAdapterStatus() {
  if (USE_MOCK) {
    await simulateLatency()
    return mockAdapters
  }
  const res = await fetch(`${API_BASE}/adapters/status`)
  if (!res.ok) throw new Error('Failed to fetch adapter status')
  return res.json()
}

export async function getFederatedEvents(filters = {}) {
  if (USE_MOCK) {
    await simulateLatency()
    return applyFilters(mockFederatedEvents, filters)
  }
  const params = new URLSearchParams(
    Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
  )
  const res = await fetch(`${API_BASE}/federated/events?${params}`)
  if (!res.ok) throw new Error('Failed to fetch federated events')
  return res.json()
}

export async function getAnalyticsSummary() {
  if (USE_MOCK) {
    await simulateLatency()
    const total = mockFederatedEvents.length
    const lowReliability = mockFederatedEvents.filter((e) => e.reliability === 'low').length
    const byDepartment = {}
    mockFederatedEvents.forEach((e) => {
      byDepartment[e.department] = (byDepartment[e.department] || 0) + 1
    })
    return {
      totalEvents: total,
      lowReliabilityCount: lowReliability,
      departmentBreakdown: byDepartment,
      headline: `${total} events federated from Model 2, correlated against Model 1's registry. ` +
        `${lowReliability} flagged low-reliability due to camera health issues.`,
    }
  }
  const res = await fetch(`${API_BASE}/federated/analytics`)
  if (!res.ok) throw new Error('Failed to fetch federated analytics')
  return res.json()
}

function applyFilters(events, { department, reliability, search } = {}) {
  return events.filter((e) => {
    if (department && e.department !== department) return false
    if (reliability && e.reliability !== reliability) return false
    if (search && !e.plateNumber.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })
}

function simulateLatency() {
  return new Promise((resolve) => setTimeout(resolve, 150))
}
