import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import { Spinner } from './components'
import AttemptResult from './pages/AttemptResult'
import { ChallengeDetail, ChallengeList } from './pages/Challenges'
import { DuelDetail, DuelList } from './pages/Duels'
import Home from './pages/Home'
import Login from './pages/Login'
import Play from './pages/Play'
import Profile from './pages/Profile'

export default function App() {
  const { user, loading, signOut } = useAuth()

  if (loading) return <Spinner />
  if (!user) return <Login />

  return (
    <>
      <nav className="nav">
        <NavLink to="/" className="brand">
          Dance<span>Duel</span>
        </NavLink>
        <NavLink to="/challenges">Challenges</NavLink>
        <NavLink to="/duels">Duels</NavLink>
        <NavLink to={`/users/${user.username}`}>Me</NavLink>
        <span className="nav-spacer" />
        <span className="muted small nav-user">
          {user.username} · {user.rating.toFixed(0)}
        </span>
        <button className="link" onClick={signOut}>
          Sign out
        </button>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/challenges" element={<ChallengeList />} />
          <Route path="/challenges/:slug" element={<ChallengeDetail />} />
          <Route path="/play/:slug" element={<Play />} />
          <Route path="/attempts/:id" element={<AttemptResult />} />
          <Route path="/duels" element={<DuelList />} />
          <Route path="/duels/:id" element={<DuelDetail />} />
          <Route path="/users/:username" element={<Profile />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  )
}
