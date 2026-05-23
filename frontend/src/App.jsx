import { useState, useEffect, useCallback } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Flame, ArrowLeft } from 'lucide-react'
import { LandingPage } from './components/LandingPage'
import { AnalysisProgress } from './components/AnalysisProgress'
import { ResultsPage } from './components/ResultsPage'
import { useWebSocket } from './hooks/useWebSocket'
import { getSessionState } from './lib/api'

function getAnalysisCount() {
  return parseInt(localStorage.getItem('roast_analysis_count') || '0')
}
function incrementAnalysisCount() {
  const count = getAnalysisCount() + 1
  localStorage.setItem('roast_analysis_count', count)
  return count
}

function VisitorCounter() {
  const [count, setCount] = useState(null)

  useEffect(() => {
    fetch('/health')
      .then(r => r.json())
      .then(d => {
        if (d.total_analyses) setCount(d.total_analyses)
      })
      .catch(() => {})
  }, [])

  if (!count) return null

  return (
    <div className="visitor-badge">
      <span className="visitor-dot" />
      <span>{count.toLocaleString()} roasts delivered</span>
    </div>
  )
}

function NavBar({ view, onBack }) {
  return (
    <nav className="roast-nav">
      <div className="flex items-center gap-2">
        <Flame size={16} className="text-orange-500" />
        <span className="roast-logo">ROAST</span>
      </div>
      <div className="flex items-center gap-3">
        <VisitorCounter />
        {view === 'analysis' && (
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-xs text-[--roast-muted] hover:text-[--roast-text] transition-colors"
          >
            <ArrowLeft size={13} />
            New roast
          </button>
        )}
      </div>
    </nav>
  )
}

function Footer() {
  return (
    <footer className="roast-footer">
      <div className="max-w-2xl mx-auto space-y-2">
        <p>
          Built by{' '}
          <a
            href="https://linkedin.com/in/sarvesh-bhattacharyya-485360270"
            target="_blank"
            rel="noopener noreferrer"
            className="text-orange-400 hover:text-orange-300 transition-colors"
          >
            Sarvesh Bhattacharyya
          </a>
        </p>
        <p className="text-[--roast-border-light]">
          Your resume is never stored. Processed by third-party AI providers for analysis only.
        </p>
      </div>
    </footer>
  )
}

function AnalysisView({ sessionId, meta }) {
  const { sections, status } = useWebSocket(sessionId)

  if (status === 'complete' || sections.review) {
    return (
      <ResultsPage
        sections={sections}
        sessionId={sessionId}
        meta={meta}
        analysisCount={getAnalysisCount()}
      />
    )
  }

  return <AnalysisProgress sessionId={sessionId} sections={sections} />
}

export default function App() {
  const [view, setView] = useState('landing')
  const [sessionId, setSessionId] = useState(null)
  const [meta, setMeta] = useState(null)

  // Session recovery on mount — if user refreshes during analysis
  useEffect(() => {
    const saved = localStorage.getItem('roast_pending_session')
    if (saved) {
      try {
        const { sid, meta: savedMeta } = JSON.parse(saved)
        getSessionState(sid).then(state => {
          if (state?.status === 'in_progress' || state?.status === 'completed') {
            setSessionId(sid)
            setMeta(savedMeta)
            setView('analysis')
          } else {
            localStorage.removeItem('roast_pending_session')
          }
        }).catch(() => {
          localStorage.removeItem('roast_pending_session')
        })
      } catch {
        localStorage.removeItem('roast_pending_session')
      }
    }
  }, [])

  const handleAnalysisStarted = useCallback((sid, metaData) => {
    incrementAnalysisCount()
    localStorage.setItem('roast_pending_session', JSON.stringify({ sid, meta: metaData }))
    setSessionId(sid)
    setMeta(metaData)
    setView('analysis')
  }, [])

  const goToLanding = useCallback(() => {
    localStorage.removeItem('roast_pending_session')
    setView('landing')
  }, [])

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--roast-bg)', color: 'var(--roast-text)' }}>
      <div className="bg-mesh" />

      <NavBar view={view} onBack={goToLanding} />

      <div className="pt-[52px]">
        <AnimatePresence mode="wait">
          {view === 'landing' && (
            <motion.div
              key="landing"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
            >
              <LandingPage onAnalysisStarted={handleAnalysisStarted} />
              <Footer />
            </motion.div>
          )}

          {view === 'analysis' && (
            <motion.div
              key="analysis"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
            >
              <AnalysisView sessionId={sessionId} meta={meta} />
              <Footer />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
