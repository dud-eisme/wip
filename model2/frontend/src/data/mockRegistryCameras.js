// Shaped like Model 1's real GET /cameras response (CameraOut), since
// registering a new source requires picking a real camera_id (a UUID)
// from the registry.

export const mockRegistryCameras = [
  { id: '11111111-1111-1111-1111-111111111111', camera_identifier: 'CAM-TEST-001', camera_name: 'Ahmedabad - Test Junction', department: 'Police' },
  { id: '22222222-2222-2222-2222-222222222222', camera_identifier: 'CAM-TEST-002', camera_name: 'Surat - Ring Road', department: 'Police' },
  { id: '33333333-3333-3333-3333-333333333333', camera_identifier: 'CAM-TEST-003', camera_name: 'Vadodara - Station Rd', department: 'Police' },
  { id: '44444444-4444-4444-4444-444444444444', camera_identifier: 'CAM-TEST-004', camera_name: 'Rajkot - Market Junction', department: 'Police' },
  { id: '55555555-5555-5555-5555-555555555555', camera_identifier: 'CAM-TEST-005', camera_name: 'Gandhinagar - Ward Office', department: 'Police' },
]
