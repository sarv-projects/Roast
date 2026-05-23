import { SkeletonLoader } from './SkeletonLoader'

export function MarketPulse({ marketContext, fullContext, loading }) {
  if (loading) return (
    <div className="space-y-3">
      <h2 className="text-xs font-semibold text-[--roast-muted] uppercase tracking-wider">Market Pulse</h2>
      <SkeletonLoader lines={4} />
    </div>
  )

  if (!marketContext) return null

  const freshness = fullContext?.distilled?.freshness_label || 'Current'
  const breaking = fullContext?.breaking_signal
  const breakingAvailable = fullContext?.breaking_available
  const skills = fullContext?.distilled?.top_required_skills?.slice(0, 5) || []
  const salary = fullContext?.distilled?.salary_band || 'data unavailable'

  const freshnessColor = {
    'Current': 'text-green-400 bg-green-500/10 border-green-500/20',
    'Recent': 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
    'Needs Refresh': 'text-red-400 bg-red-500/10 border-red-500/20',
  }[freshness] || 'text-[--roast-muted]'

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold text-[--roast-muted] uppercase tracking-wider">Market Pulse</h2>
        <span className={`text-xs px-2 py-0.5 rounded-full border font-mono ${freshnessColor}`}>{freshness}</span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <p className="text-xs text-[--roast-muted]">Sentiment</p>
          <p className="text-sm text-[--roast-text] leading-relaxed">{marketContext.live_context_summary}</p>
        </div>
        <div className="space-y-1.5">
          <p className="text-xs text-[--roast-muted]">Salary band</p>
          <p className="text-sm text-[--roast-text] font-mono">{salary}</p>
        </div>
      </div>

      {skills.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-[--roast-muted]">Top skills</p>
          <div className="flex flex-wrap gap-2">
            {skills.map(s => (
              <span key={s} className="text-xs font-mono px-2.5 py-1 bg-orange-500/8 border border-orange-500/20 rounded-md text-orange-300">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-1.5">
        <p className="text-xs text-[--roast-muted]">Competitive pool</p>
        <p className="text-sm text-[--roast-text] leading-relaxed">{marketContext.competitive_pool_description?.slice(0, 120)}{marketContext.competitive_pool_description?.length > 120 ? '…' : ''}</p>
      </div>

      <div className="h-px bg-[--roast-border]" />
      <p className="text-xs text-[--roast-muted]">
        Breaking signal:{' '}
        {breakingAvailable
          ? <span className="text-green-400">{breaking}</span>
          : <span className="text-[--roast-placeholder]">⚠ Unavailable — showing cached intel</span>
        }
      </p>
    </div>
  )
}
