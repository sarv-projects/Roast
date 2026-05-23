import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  CheckCircle, AlertTriangle, BookOpen, BarChart2,
  Zap, AlignLeft, MessageCircle, ChevronDown,
} from 'lucide-react'
import { SkeletonLoader } from './SkeletonLoader'
import { useInferenceToggle } from '../hooks/useInferenceToggle'
import { submitFollowup } from '../lib/api'

const SECTION_CONFIG = {
  working:     { icon: CheckCircle,   color: 'text-emerald-400', border: 'border-emerald-500/25', bg: 'bg-emerald-500/4',  accent: '#34d399' },
  hurting:     { icon: AlertTriangle, color: 'text-red-400',     border: 'border-red-500/25',     bg: 'bg-red-500/4',      accent: '#f87171' },
  career:      { icon: BookOpen,      color: 'text-blue-400',    border: 'border-blue-500/25',    bg: 'bg-blue-500/4',     accent: '#60a5fa' },
  competitive: { icon: BarChart2,     color: 'text-purple-400',  border: 'border-purple-500/25',  bg: 'bg-purple-500/4',   accent: '#c084fc' },
  action:      { icon: Zap,           color: 'text-orange-400',  border: 'border-orange-500/25',  bg: 'bg-orange-500/4',   accent: '#fb923c' },
  jd:          { icon: AlignLeft,     color: 'text-cyan-400',    border: 'border-cyan-500/25',    bg: 'bg-cyan-500/4',     accent: '#22d3ee' },
}

/**
 * Parse inference chains from prose text.
 * Looks for "Recruiter sees X → assumes Y → decides Z" patterns.
 * Returns array of { type: 'chain'|'text', content } segments.
 */
function parseContent(text) {
  if (!text) return []
  const segments = []
  // Split on sentence boundaries, then look for inference chain patterns
  const lines = text.split(/\n+/)
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) continue
    // Detect inference chain: contains → or "recruiter sees"
    if ((trimmed.includes('→') || trimmed.includes('->')) &&
        (trimmed.toLowerCase().includes('recruiter') || trimmed.toLowerCase().includes('sees') || trimmed.toLowerCase().includes('assumes'))) {
      segments.push({ type: 'chain', content: trimmed })
    } else {
      segments.push({ type: 'text', content: trimmed })
    }
  }
  return segments
}

/**
 * Parse action plan into numbered steps.
 * Looks for "1)", "1.", "Step 1" patterns.
 */
function parseActionPlan(text) {
  if (!text) return null
  const stepPattern = /(?:^|\n)\s*(?:\d+[\.\)]|Step\s+\d+:?)\s+/gim
  const hasSteps = stepPattern.test(text)
  if (!hasSteps) return null

  const steps = text
    .split(/\n/)
    .map(l => l.trim())
    .filter(Boolean)
    .reduce((acc, line) => {
      if (/^\d+[\.\)]\s+/.test(line) || /^Step\s+\d+/i.test(line)) {
        acc.push(line.replace(/^\d+[\.\)]\s+/, '').replace(/^Step\s+\d+:?\s*/i, ''))
      } else if (acc.length > 0) {
        acc[acc.length - 1] += ' ' + line
      } else {
        acc.push(line)
      }
      return acc
    }, [])

  return steps.length >= 2 ? steps : null
}

function InferenceChain({ content }) {
  const parts = content.split(/→|->/).map(p => p.trim()).filter(Boolean)
  return (
    <div className="my-3 rounded-lg bg-[--roast-surface-2] border border-[--roast-border] px-3 py-2.5 text-xs font-mono">
      <div className="flex flex-wrap items-center gap-1.5">
        {parts.map((part, i) => (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && <span className="text-[--roast-placeholder]">→</span>}
            <span className={
              i === 0 ? 'text-[--roast-muted]' :
              i === parts.length - 1 ? 'text-red-400' :
              'text-yellow-400/80'
            }>{part}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

function ActionSteps({ steps }) {
  return (
    <ol className="space-y-2.5 mt-1">
      {steps.map((step, i) => (
        <li key={i} className="flex items-start gap-3">
          <span className="shrink-0 w-5 h-5 rounded-full bg-orange-500/15 border border-orange-500/25 flex items-center justify-center text-[10px] font-bold text-orange-400 mt-0.5">
            {i + 1}
          </span>
          <p className="text-sm text-[--roast-text-2] leading-relaxed flex-1">{step}</p>
        </li>
      ))}
    </ol>
  )
}

function SectionContent({ content, configKey, showInference }) {
  const isAction = configKey === 'action'
  const isHurting = configKey === 'hurting'

  // Try to parse action plan as steps
  if (isAction) {
    const steps = parseActionPlan(content)
    if (steps) return <ActionSteps steps={steps} />
  }

  // Parse inference chains for hurting section
  if (isHurting && showInference) {
    const segments = parseContent(content)
    return (
      <div>
        {segments.map((seg, i) => (
          seg.type === 'chain'
            ? <InferenceChain key={i} content={seg.content} />
            : <p key={i} className="text-sm text-[--roast-text-2] leading-[1.8] mb-2">{seg.content}</p>
        ))}
      </div>
    )
  }

  return <p className="text-sm text-[--roast-text-2] leading-[1.8] whitespace-pre-wrap">{content}</p>
}

function Section({ title, content, followups, sessionId, sectionKey, configKey, showInference, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  const [usedFollowup, setUsedFollowup] = useState(false)
  const [activeQuestion, setActiveQuestion] = useState(null)
  const [answer, setAnswer] = useState('')
  const [loadingAnswer, setLoadingAnswer] = useState(false)
  const cfg = SECTION_CONFIG[configKey] || SECTION_CONFIG.action
  const Icon = cfg.icon

  const handleFollowup = async (question) => {
    if (usedFollowup) return
    setActiveQuestion(question)
    setLoadingAnswer(true)
    try {
      const res = await submitFollowup({ sessionId, section: sectionKey, question })
      setAnswer(res.answer)
      setUsedFollowup(true)
    } catch {
      setAnswer('Unable to load answer. Please try again.')
    }
    setLoadingAnswer(false)
  }

  return (
    <div className={`rounded-xl border ${cfg.border} overflow-hidden`} style={{ background: 'var(--roast-surface)' }}>
      {/* Header — always visible, clickable */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3.5 hover:bg-white/[0.02] transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <div className={`w-6 h-6 rounded-lg flex items-center justify-center`} style={{ background: `${cfg.accent}18` }}>
            <Icon size={13} style={{ color: cfg.accent }} />
          </div>
          <span className="text-sm font-semibold text-[--roast-text]">{title}</span>
        </div>
        <motion.div animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.2 }}>
          <ChevronDown size={14} className="text-[--roast-placeholder]" />
        </motion.div>
      </button>

      {/* Collapsible body */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            style={{ overflow: 'hidden' }}
          >
            <div className={`px-4 pb-4 pt-1 border-t ${cfg.border}`}>
              <SectionContent content={content} configKey={configKey} showInference={showInference} />

              {/* Follow-up questions */}
              {followups?.length > 0 && !usedFollowup && (
                <div className="flex flex-wrap gap-2 mt-4">
                  {followups.map((q, i) => (
                    <button key={i} onClick={() => handleFollowup(q)} className="followup-pill">
                      <MessageCircle size={10} />
                      {q}
                    </button>
                  ))}
                </div>
              )}

              {/* Follow-up answer */}
              <AnimatePresence>
                {activeQuestion && (
                  <motion.div
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    className={`mt-4 border-l-2 ${cfg.border} pl-4 space-y-2`}
                  >
                    <p className="text-xs text-[--roast-muted] italic">{activeQuestion}</p>
                    {loadingAnswer
                      ? <SkeletonLoader lines={2} />
                      : <p className="text-sm text-[--roast-text-2] leading-relaxed">{answer}</p>
                    }
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export function ReviewDocument({ review, sessionId, loading }) {
  const [showInference, setShowInference] = useInferenceToggle()

  if (loading) return (
    <div className="space-y-3">
      <div className="flex items-center justify-between mb-1">
        <div className="section-label">The Review</div>
      </div>
      <SkeletonLoader lines={8} />
    </div>
  )

  if (!review) return null

  return (
    <div className="space-y-2.5">
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <div className="section-label">The Review</div>
        <button
          onClick={() => setShowInference(v => !v)}
          className="text-xs text-[--roast-muted] hover:text-[--roast-text] transition-colors flex items-center gap-1.5"
        >
          Inference chains:{' '}
          <span className={`font-semibold ${showInference ? 'text-orange-400' : 'text-[--roast-placeholder]'}`}>
            {showInference ? 'ON' : 'OFF'}
          </span>
        </button>
      </div>

      <Section title="What's Working"      content={review.whats_working_section}      followups={review.six_second_followups}    sessionId={sessionId} sectionKey="six_second"    configKey="working"     showInference={showInference} defaultOpen={true} />
      <Section title="What's Hurting You"  content={review.whats_hurting_section}      followups={review.whats_hurting_followups} sessionId={sessionId} sectionKey="whats_hurting" configKey="hurting"     showInference={showInference} defaultOpen={true} />
      <Section title="Career Story"        content={review.career_story_section}       followups={review.career_story_followups}  sessionId={sessionId} sectionKey="career_story"  configKey="career"      showInference={showInference} defaultOpen={false} />
      <Section title="Competitive Position" content={review.competitive_position_section} followups={review.competitive_followups} sessionId={sessionId} sectionKey="competitive"   configKey="competitive" showInference={showInference} defaultOpen={false} />
      <Section title="Action Plan"         content={review.action_plan_section}        followups={[]}                             sessionId={sessionId} sectionKey="action_plan"   configKey="action"      showInference={showInference} defaultOpen={true} />

      {review.jd_alignment_section && (
        <Section title="JD Alignment" content={review.jd_alignment_section} followups={[]} sessionId={sessionId} sectionKey="jd_alignment" configKey="jd" showInference={showInference} defaultOpen={true} />
      )}
    </div>
  )
}
