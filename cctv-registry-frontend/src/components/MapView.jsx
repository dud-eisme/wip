import { MapContainer, TileLayer, CircleMarker, Popup } from 'react-leaflet'

const STATUS_COLOR = {
  active: '#1e7a4c',
  maintenance: '#b3720f',
  inactive: '#b13a2e',
}

const GUJARAT_CENTER = [22.4, 72.0]

export default function MapView({ cameras }) {
  return (
    <div className="map-area">
      <MapContainer center={GUJARAT_CENTER} zoom={7} scrollWheelZoom={true}>
        <TileLayer
          attribution='&copy; OpenStreetMap contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {cameras.map((cam) => (
          <CircleMarker
            key={cam.id}
            center={[cam.lat, cam.lng]}
            radius={6}
            pathOptions={{
              color: STATUS_COLOR[cam.healthStatus],
              fillColor: STATUS_COLOR[cam.healthStatus],
              fillOpacity: 0.85,
              weight: 1.5,
            }}
          >
            <Popup>
              <div style={{ fontFamily: 'var(--font-ui)', fontSize: 12, minWidth: 180 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{cam.name}</div>
                <div style={{ color: '#4b5566', marginBottom: 6 }}>{cam.id}</div>
                <Row label="Department" value={cam.department} />
                <Row label="Type" value={cam.cameraType} />
                <Row label="Ownership" value={cam.ownership} />
                <Row label="Connectivity" value={cam.connectivityStatus} />
                <Row label="Storage" value={cam.storageDetails} />
              </div>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>

      <div className="map-legend">
        <LegendItem color={STATUS_COLOR.active} label="Active" />
        <LegendItem color={STATUS_COLOR.maintenance} label="Maintenance" />
        <LegendItem color={STATUS_COLOR.inactive} label="Offline" />
      </div>
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '2px 0' }}>
      <span style={{ color: '#4b5566' }}>{label}</span>
      <span style={{ fontWeight: 500 }}>{value}</span>
    </div>
  )
}

function LegendItem({ color, label }) {
  return (
    <div className="map-legend__item">
      <span className="map-legend__dot" style={{ background: color }} />
      {label}
    </div>
  )
}
