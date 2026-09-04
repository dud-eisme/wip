import { FILTER_OPTIONS } from '../data/mockCameras'

export default function FilterPanel({ filters, onChange }) {
  const update = (key) => (e) => onChange({ ...filters, [key]: e.target.value || undefined })

  return (
    <div>
      <p className="panel-section__title">FILTERS</p>

      <div className="filter-group">
        <label htmlFor="dept">Department</label>
        <select id="dept" value={filters.department || ''} onChange={update('department')}>
          <option value="">All departments</option>
          {FILTER_OPTIONS.departments.map((d) => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>
      </div>

      <div className="filter-group">
        <label htmlFor="type">Camera type</label>
        <select id="type" value={filters.cameraType || ''} onChange={update('cameraType')}>
          <option value="">All types</option>
          {FILTER_OPTIONS.cameraTypes.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>

      <div className="filter-group">
        <label htmlFor="status">Status</label>
        <select id="status" value={filters.status || ''} onChange={update('status')}>
          <option value="">All statuses</option>
          {FILTER_OPTIONS.status.map((s) => (
            <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>
          ))}
        </select>
      </div>
    </div>
  )
}
