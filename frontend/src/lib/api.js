const BASE = '/api'
const TIMEOUT = 30000

async function fetchWithTimeout(url, options = {}, timeout = TIMEOUT) {
  const controller = new AbortController()
  const id = setTimeout(() => controller.abort(), timeout)
  try {
    const res = await fetch(url, { ...options, signal: controller.signal })
    return res
  } finally {
    clearTimeout(id)
  }
}

export async function sessionInit({ role, market, company_type, experience_level }) {
  const res = await fetchWithTimeout(`${BASE}/session-init`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, market, company_type, experience_level }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function submitAnalysis({ sessionId, file, role, company_type, market, experience_level, userContext, jdText, githubUrl, optedInCorpus }) {
  const form = new FormData()
  form.append('session_id', sessionId)
  form.append('role', role)
  form.append('company_type', company_type)
  form.append('market', market)
  form.append('experience_level', experience_level)
  form.append('user_context', userContext || '')
  form.append('jd_text', jdText || '')
  form.append('github_url', githubUrl || '')
  form.append('opted_in_corpus', optedInCorpus ? 'true' : 'false')
  form.append('file', file)

  const res = await fetchWithTimeout(`${BASE}/analyse`, { method: 'POST', body: form })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(body)
  }
  return res.json()
}

export async function getSessionState(sessionId) {
  const res = await fetchWithTimeout(`${BASE}/session/${sessionId}/state`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function submitFollowup({ sessionId, section, question }) {
  const res = await fetchWithTimeout(`${BASE}/followup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, section, question }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function submitFeedback({ sessionId, useful, role, market, company_type }) {
  try {
    await fetchWithTimeout(`${BASE}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, useful, role, market, company_type }),
    })
  } catch (e) {
    // silently ignore — feedback is fire-and-forget
  }
}

export async function requestToken(email) {
  const res = await fetchWithTimeout(`${BASE}/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function verifyToken({ token, sessionId }) {
  const res = await fetchWithTimeout(`${BASE}/token/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, session_id: sessionId }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function createWebSocket(sessionId) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return new WebSocket(`${proto}//${window.location.host}/api/ws/${sessionId}`)
}
