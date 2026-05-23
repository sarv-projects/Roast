import { Component } from 'react'

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: 'var(--roast-bg)', color: 'var(--roast-text)' }}>
          <div className="text-center space-y-4 px-4">
            <h1 className="text-3xl font-bold">🔥</h1>
            <p className="text-[--roast-muted]">Something went wrong. Try reloading.</p>
            <button onClick={() => window.location.reload()} className="roast-btn px-6 py-2.5 text-sm mt-2">
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
