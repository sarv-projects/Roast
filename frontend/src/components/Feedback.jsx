import { useState } from 'react'
import { submitFeedback, requestToken } from '../lib/api'

export function FeedbackButton({ sessionId, role, market, companyType }) {
  const [voted, setVoted] = useState(null)

  const vote = async (useful) => {
    if (voted) return
    setVoted(useful)
    try {
      await submitFeedback({ sessionId, useful, role, market, company_type: companyType })
    } catch (e) {
      console.error('feedback_submit_failed', e)
    }
  }

  if (voted !== null) {
    return <p className="text-xs text-[--roast-muted] text-center py-2">Thanks for the feedback.</p>
  }

  return (
    <div className="flex items-center justify-center gap-4 py-2">
      <p className="text-xs text-[--roast-muted]">Was this review useful?</p>
      <button onClick={() => vote(true)} className="text-lg hover:scale-110 transition-transform">👍</button>
      <button onClick={() => vote(false)} className="text-lg hover:scale-110 transition-transform">👎</button>
    </div>
  )
}

export function ThirdAnalysisUnlock() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const send = async () => {
    if (!email || loading) return
    setLoading(true)
    setError('')
    try {
      await requestToken(email)
      setSent(true)
    } catch (e) {
      setError(e.message || 'Failed to send token.')
    }
    setLoading(false)
  }

  if (sent) {
    return (
      <div className="roast-card text-center space-y-1">
        <p className="text-sm text-[--roast-text]">Token sent to {email}</p>
        <p className="text-xs text-[--roast-muted]">Check your inbox. Valid for 24 hours.</p>
      </div>
    )
  }

  return (
    <div className="roast-card space-y-4">
      <div>
        <p className="text-sm font-medium text-[--roast-text]">Get one more free roast</p>
        <p className="text-xs text-[--roast-muted] mt-0.5">Enter your email and we'll send a token.</p>
      </div>
      <div className="flex gap-2">
        <input
          value={email}
          onChange={e => setEmail(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && send()}
          placeholder="your@email.com"
          type="email"
          className="roast-input flex-1 min-w-0 px-3 py-2.5 text-sm"
        />
        <button
          onClick={send}
          disabled={loading || !email}
          className="roast-btn shrink-0 px-4 py-2.5 text-sm"
        >
          {loading ? '...' : 'Send'}
        </button>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  )
}
