import { Component } from 'react'

/**
 * Catches uncaught errors anywhere in the child component tree.
 * Without this, a single unhandled exception causes a blank white screen.
 *
 * Usage:
 *   <ErrorBoundary>
 *     <Leaderboard />
 *   </ErrorBoundary>
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, message: '' }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, message: error?.message ?? String(error) }
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center p-8">
          <div className="max-w-lg w-full bg-red-950/60 border border-red-700 rounded-xl p-6">
            <h2 className="text-xl font-bold text-red-400 mb-2">Something went wrong</h2>
            <p className="text-sm text-gray-300 mb-4">{this.state.message}</p>
            <button
              onClick={() => this.setState({ hasError: false, message: '' })}
              className="bg-red-700 hover:bg-red-600 text-white px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
            >
              Try again
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
