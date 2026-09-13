// Model 2 has no login of its own — it validates Model 1's JWT directly
// (same SECRET_KEY, same users table). So this frontend logs in against
// Model 1 and reuses that one token for every Model 2 API call too.

const MODEL1_API_BASE = import.meta.env.VITE_MODEL1_API_BASE || 'http://localhost:8000/api/v1'

export async function login(email, password) {
  const body = new URLSearchParams()
  body.append('username', email)
  body.append('password', password)

  const res = await fetch(`${MODEL1_API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })

  if (!res.ok) {
    throw new Error('Login failed — check email/password')
  }

  const data = await res.json()
  return data.access_token
}
