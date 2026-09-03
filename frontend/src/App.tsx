import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import RoleDetail from './pages/RoleDetail'
import RoleEdit from './pages/RoleEdit'
import RoleRequirements from './pages/RoleRequirements'
import Import from './pages/Import'
import Profile from './pages/Profile'
import Profile360 from './pages/Profile360'
import Comparison from './pages/Comparison'
import Preferences from './pages/Preferences'
import Space from './pages/Space'
import Targets from './pages/Targets'
import AddTarget from './pages/AddTarget'
import Episodes from './pages/Episodes'
import Vocabulary from './pages/Vocabulary'
import Capabilities from './pages/Capabilities'
import CapabilityCoverage from './pages/CapabilityCoverage'

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
        <NavLink to="/vocabulary" className={({ isActive }) => (isActive ? 'active' : '')}>
          Vocabulary
        </NavLink>
        <NavLink to="/capabilities" className={({ isActive }) => (isActive ? 'active' : '')}>
          Capabilities
        </NavLink>
        <NavLink to="/coverage" className={({ isActive }) => (isActive ? 'active' : '')}>
          Coverage
        </NavLink>
        <NavLink to="/profile360" className={({ isActive }) => (isActive ? 'active' : '')}>
          profile360
        </NavLink>
        <NavLink to="/preferences" className={({ isActive }) => (isActive ? 'active' : '')}>
          Preferences
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
        <Route path="/vocabulary" element={<Vocabulary />} />
        <Route path="/capabilities" element={<Capabilities />} />
        <Route path="/coverage" element={<CapabilityCoverage />} />
        <Route path="/profile360" element={<Profile360 />} />
        <Route path="/preferences" element={<Preferences />} />
        <Route path="/import" element={<Import />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/roles/:id" element={<RoleDetail />} />
        <Route path="/roles/:id/edit" element={<RoleEdit />} />
        <Route path="/role-instances/:id/requirements" element={<RoleRequirements />} />
        <Route path="/comparison/:id" element={<Comparison />} />
      </Routes>
    </div>
  )
}

export default App
