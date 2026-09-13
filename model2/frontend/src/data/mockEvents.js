import { mockSources } from './mockSources'

const SAMPLE_PLATES = ['GJ01AB1234', 'GJ05CD5678', 'GJ18EF9012', 'GJ06GH3456', 'GJ27JK7890']

function generateEvents(count = 15) {
  const events = []
  for (let i = 1; i <= count; i++) {
    const source = mockSources[Math.floor(Math.random() * mockSources.length)]
    events.push({
      id: `evt-${String(i).padStart(4, '0')}`,
      job_id: `job-${String(Math.ceil(i / 5)).padStart(4, '0')}`,
      camera_id: source.camera_id,
      source_id: source.id,
      plate_text: SAMPLE_PLATES[Math.floor(Math.random() * SAMPLE_PLATES.length)],
      confidence: Math.round((0.55 + Math.random() * 0.4) * 100) / 100,
      detected_at: new Date(Date.now() - Math.floor(Math.random() * 72) * 3600000).toISOString(),
      frame_number: Math.floor(Math.random() * 5000),
      snapshot_path: null, // mock mode has no real snapshot files to serve
      is_flagged: Math.random() < 0.15,
      flagged_note: null,
    })
  }
  return events.sort((a, b) => new Date(b.detected_at) - new Date(a.detected_at))
}

export const mockEvents = generateEvents()
