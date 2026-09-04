const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'

export async function login(email, password) {
  const body = new URLSearchParams()
  body.append('username', email)
  body.append('password', password)

  const res = await fetch(`${API_BASE}/auth/login`, {
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
