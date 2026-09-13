import { useEffect, useState } from 'react'
import ConnectorStatusPanel from './components/ConnectorStatusPanel'
import AnalyticsSummary from './components/AnalyticsSummary'
import FederatedEventsTable from './components/FederatedEventsTable'
import { getAdapterStatus, getFederatedEvents, getAnalyticsSummary } from './api/federation'

export default function App() {
  const [adapters, setAdapters] = useState([])
  const [events, setEvents] = useState([])
  const [summary, setSummary] = useState(null)
  const [searchInput, setSearchInput] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    const timerId = setTimeout(() => setDebounced(searchInput), 300)
    return () => clearTimeout(timerId)
  }, [searchInput])

  useEffect(() => {
    getAdapterStatus().then(setAdapters)
    getAnalyticsSummary().then(setSummary)
  }, [])

  useEffect(() => {
    getFederatedEvents({ search: debounced }).then(setEvents)
  }, [debounced])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__title">
          VMS Federation &amp; Middleware
          <small>Model 3 · Gujarat Police Innovation Hackathon 2026</small>
        </div>

        <div className="topbar__search">
          <input
            type="text"
            placeholder="Search by plate number…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
      </header>

      <main className="main-area">
        <ConnectorStatusPanel adapters={adapters} />
        <AnalyticsSummary summary={summary} />
        <FederatedEventsTable events={events} />
      </main>
    </div>
  )
}
