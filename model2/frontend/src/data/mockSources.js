import { mockRegistryCameras } from './mockRegistryCameras'

function generateSources() {
  return mockRegistryCameras.map((cam, i) => ({
    id: `src-${String(i + 1).padStart(4, '0')}`,
    camera_id: cam.id,
    camera_name: cam.camera_name, // denormalized for display only - real API doesn't include this, frontend joins it
    source_name: `${cam.camera_name} Feed`,
    source_type: 'file', // mock: pretend everything is a local test clip
    source_url: `/demo-clips/${cam.camera_identifier}.mp4`,
    is_active: i !== 3, // mirrors Model 1's Rajkot test camera being marked inactive
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }))
}

export const mockSources = generateSources()

// Mock worker status - separate from the source record itself, since in
// reality a source can exist without its worker currently running.
export const mockWorkerStatus = {}
mockSources.forEach((s, i) => {
  mockWorkerStatus[s.id] = {
    source_id: s.id,
    is_running: i < 2, // first two "already running" for demo purposes
    frames_captured: i < 2 ? 340 + i * 12 : 0,
    last_frame_at: i < 2 ? new Date().toISOString() : null,
    last_error: i === 3 ? 'Could not open source: camera marked inactive' : null,
  }
})
