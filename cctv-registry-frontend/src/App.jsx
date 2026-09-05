import { useEffect, useState } from 'react'
import MapView from './components/MapView'
import FilterPanel from './components/FilterPanel'
import GapAnalysisPanel from './components/GapAnalysisPanel'
import CameraList from './components/CameraList'
import { getCameras, getGapAnalysis } from './api/registry'
import { exportCamerasToCsv } from './utils/exportCsv'

export default function App() {
  const [filters, setFilters] = useState({})
  const [searchInput, setSearchInput] = useState('')
  const [cameras, setCameras] = useState([])
  const [gapData, setGapData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    setLoading(true)
    getCameras({ ...filters, search: searchInput }).then((data) => {
      if (active) {
        setCameras(data)
        setLoading(false)
      }
    })
    return () => { active = false }
  }, [filters, searchInput])

  useEffect(() => {
    getGapAnalysis().then(setGapData)
  }, [])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__title">
          CCTV Registry &amp; GIS Foundation
          <small>Model 1 · Gujarat Police Innovation Hackathon 2026</small>
        </div>

        <div className="topbar__search">
          <input
            type="text"
            placeholder="Search cameras by name…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>

        <div className="topbar__actions">
          <button className="btn" onClick={() => exportCamerasToCsv(cameras)}>
            Export CSV
          </button>
          <button className="btn btn--primary">Onboard camera</button>
        </div>
      </header>

      <div className="main-split">
        <aside className="sidebar">
          <FilterPanel filters={filters} onChange={setFilters} />
          <GapAnalysisPanel gapData={gapData} />
          <CameraList cameras={cameras} />
        </aside>

        <MapView cameras={cameras} />
      </div>

      {loading && cameras.length === 0 && (
        <div style={{ position: 'fixed', bottom: 12, right: 12, fontSize: 12, color: 'var(--ink-soft)' }}>
          Loading cameras…
        </div>
      )}
    </div>
  )
}
