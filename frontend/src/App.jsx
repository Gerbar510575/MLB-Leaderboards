import ErrorBoundary from './components/ErrorBoundary'
import Leaderboard   from './components/Leaderboard'

export default function App() {
  return (
    <ErrorBoundary>
      <Leaderboard />
    </ErrorBoundary>
  )
}
