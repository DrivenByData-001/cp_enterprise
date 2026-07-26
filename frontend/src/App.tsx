import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import RoleDetail from './pages/RoleDetail'
import RoleEdit from './pages/RoleEdit'
import Import from './pages/Import'
import Profile from './pages/Profile'
import Space from './pages/Space'
import Targets from './pages/Targets'
import AddTarget from './pages/AddTarget'
import Episodes from './pages/Episodes'

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
        <NavLink to="/targets" className={({ isActive }) => (isActive ? 'active' : '')}>
          Targets
        </NavLink>
        <NavLink to="/episodes" className={({ isActive }) => (isActive ? 'active' : '')}>
          History
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
        <Route path="/targets" element={<Targets />} />
        <Route path="/targets/new" element={<AddTarget />} />
        <Route path="/episodes" element={<Episodes />} />
        <Route path="/import" element={<Import />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/roles/:id" element={<RoleDetail />} />
        <Route path="/roles/:id/edit" element={<RoleEdit />} />
      </Routes>
    </div>
  )
}

export default App
