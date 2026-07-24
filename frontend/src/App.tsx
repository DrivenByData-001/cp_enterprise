import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import RoleDetail from './pages/RoleDetail'
import Import from './pages/Import'
import Profile from './pages/Profile'
import Space from './pages/Space'

function App() {
  return (
    <div className="app-shell">
      <nav className="nav">
        <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>
          Dashboard
        </NavLink>
        <NavLink to="/space" className={({ isActive }) => (isActive ? 'active' : '')}>
          Space
        </NavLink>
        <NavLink to="/import" className={({ isActive }) => (isActive ? 'active' : '')}>
          Import
        </NavLink>
        <NavLink to="/profile" className={({ isActive }) => (isActive ? 'active' : '')}>
          Profile
        </NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/space" element={<Space />} />
        <Route path="/import" element={<Import />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/roles/:id" element={<RoleDetail />} />
      </Routes>
    </div>
  )
}

export default App
