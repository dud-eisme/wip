import { useState } from 'react'
import { login } from '../api/auth'

export default function LoginScreen({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const token = await login(email, password)
      onLogin(token)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <h1>CCTV Registry &amp; GIS Foundation</h1>
        <p>Model 1 · Gujarat Police Innovation Hackathon 2026</p>

        <form onSubmit={handleSubmit}>
          <div className="form-row">
            <label>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          </div>
          <div className="form-row">
            <label>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>

          {error && <div className="form-error">{error}</div>}

          <button type="submit" className="btn btn--primary" disabled={submitting} style={{ marginTop: 4 }}>
            {submitting ? 'Signing in…' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  )
}
