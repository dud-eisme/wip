// This is the actual "federation" output: Model 2's raw ANPR event, joined
// with Model 1's camera metadata (department, health status). In production
// this join happens server-side in the correlation engine, using each
// adapter's live data — see api/federation.js for the real-API shape.

const CAMERA_CONTEXT = {
  'CAM-TEST-001': { department: 'Police', healthStatus: 'active' },
  'CAM-TEST-002': { department: 'Police', healthStatus: 'active' },
  'CAM-TEST-003': { department: 'Police', healthStatus: 'maintenance' },
  'CAM-TEST-004': { department: 'Police', healthStatus: 'inactive' },
  'CAM-TEST-005': { department: 'Police', healthStatus: 'active' },
}

const SAMPLE_PLATES = ['GJ01AB1234', 'GJ05CD5678', 'GJ18EF9012', 'GJ06GH3456', 'GJ27JK7890']
const CAMERA_IDS = Object.keys(CAMERA_CONTEXT)

function computeReliability(healthStatus) {
  // The actual "correlation" logic: an event captured on a camera that's
  // offline or flagged for maintenance is less trustworthy than one from
  // a healthy camera. This is the "coordinated monitoring" value-add.
  if (healthStatus === 'inactive') return 'low'
  if (healthStatus === 'maintenance') return 'medium'
  return 'high'
}

function generateFederatedEvents(count = 15) {
  const events = []
  for (let i = 1; i <= count; i++) {
    const cameraId = CAMERA_IDS[Math.floor(Math.random() * CAMERA_IDS.length)]
    const context = CAMERA_CONTEXT[cameraId]
    events.push({
      id: `FED-${String(i).padStart(4, '0')}`,
      plateNumber: SAMPLE_PLATES[Math.floor(Math.random() * SAMPLE_PLATES.length)],
      cameraId,
      department: context.department,
      cameraHealthStatus: context.healthStatus,
      reliability: computeReliability(context.healthStatus),
      timestamp: new Date(Date.now() - Math.floor(Math.random() * 72) * 3600000).toISOString(),
      sourceModel: 'Model 2',
    })
  }
  return events.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
}

export const mockFederatedEvents = generateFederatedEvents()
