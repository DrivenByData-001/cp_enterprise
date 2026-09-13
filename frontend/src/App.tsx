import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './useAuth'
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
import Trends from './pages/Trends'
import Economics from './pages/Economics'

function App() {
  const { logout } = useAuth()
  const { pathname } = useLocation()
  const groups = [
    { label: 'Explore roles', links: [['/', 'Roles'], ['/space', 'Role map'], ['/trends', 'Trends'], ['/economics', 'Economics'], ['/targets', 'Targets']] },
    { label: 'My evidence', links: [['/profile', 'Profile overview'], ['/profile360', 'Evidence and mappings'], ['/coverage', 'Capability coverage'], ['/episodes', 'Career history'], ['/preferences', 'Preferences']] },
    { label: 'Manage vocabulary', links: [['/vocabulary', 'Vocabulary'], ['/capabilities', 'Capability catalogue']] },
  ]

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="site-header"><strong>Career Navigator</strong></header>
      <nav className="nav" aria-label="Main navigation">
        {groups.map(group => <details key={group.label} className="nav-group">
          <summary className={group.links.some(([path]) => pathname === path || (path !== '/' && pathname.startsWith(path + '/'))) ? 'active' : ''}>{group.label}</summary>
          <div className="nav-menu">{group.links.map(([path, label]) => <NavLink key={path} to={path} end={path === '/'} onClick={e => e.currentTarget.closest('details')?.removeAttribute('open')} className={({ isActive }) => isActive ? 'active' : ''}>{label}</NavLink>)}</div>
        </details>)}
        <NavLink to="/import" className={({ isActive }) => isActive ? 'active' : ''}>Add posting</NavLink>
        <button type="button" onClick={logout} title="Sign out of Career Navigator">Log out</button>
      </nav>
      <main id="main-content">
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/space" element={<Space />} />
        <Route path="/trends" element={<Trends />} />
        <Route path="/economics" element={<Economics />} />
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
        <Route path="*" element={<div><h1>Page not found</h1><NavLink to="/">Return to roles</NavLink></div>} />
      </Routes>
      </main>
    </div>
  )
}

export default App
