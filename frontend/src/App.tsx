import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './useAuth'
import { hasLegacyRoleListQuery } from './lib/roleNavigation'
import Home from './pages/Home'
import Explore from './pages/Explore'
import Applications from './pages/Applications'
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
import Pathways from './pages/Pathways'

// `/` used to be the Roles list. Old bookmarks/links carrying its query
// params (`?period=current` etc.) must keep working as Opportunities links,
// never silently reinterpreted as Home — but an unrelated query string on
// `/` should still render Home, since Home may grow its own params later.
function Root() {
  const location = useLocation()
  if (hasLegacyRoleListQuery(location.search)) {
    return <Navigate to={`/opportunities${location.search}`} replace />
  }
  return <Home />
}

// Primary destinations are always visible; a route not listed here (role/
// comparison/pathway/target detail pages) still lights up the primary tab it
// conceptually belongs under, so the shell never looks like it's lost track
// of where you are.
const PRIMARY_LINKS: { path: string; label: string; match: (pathname: string) => boolean }[] = [
  { path: '/', label: 'Home', match: (p) => p === '/' },
  {
    path: '/future',
    label: 'Explore my future',
    match: (p) => p === '/future' || p === '/targets' || p.startsWith('/targets/') || p === '/pathways' || p.startsWith('/pathways/'),
  },
  {
    path: '/opportunities',
    label: 'Opportunities',
    match: (p) => p === '/opportunities' || p.startsWith('/roles/') || p.startsWith('/comparison/') || p.startsWith('/role-instances/'),
  },
  { path: '/applications', label: 'Applications', match: (p) => p === '/applications' },
]

function App() {
  const { logout } = useAuth()
  const { pathname } = useLocation()
  const secondaryGroups = [
    {
      label: 'My profile & evidence',
      links: [
        ['/profile', 'Profile overview'],
        ['/profile360', 'Evidence and mappings'],
        ['/episodes', 'Career history'],
        ['/coverage', 'Capability coverage'],
        ['/preferences', 'Preferences'],
      ],
    },
    {
      label: 'Data & models',
      links: [
        ['/vocabulary', 'Vocabulary'],
        ['/capabilities', 'Capability catalogue'],
        ['/space', 'Role map'],
        ['/trends', 'Trends'],
        ['/economics', 'Economics'],
      ],
    },
  ]

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="site-header"><strong>Career Navigator</strong></header>
      <nav className="nav" aria-label="Main navigation">
        <div className="nav-primary">
          {PRIMARY_LINKS.map(({ path, label, match }) => (
            <NavLink key={path} to={path} end={path === '/'} className={() => (match(pathname) ? 'active' : '')}>
              {label}
            </NavLink>
          ))}
        </div>
        <div className="nav-secondary">
          {secondaryGroups.map((group) => (
            <details key={group.label} className="nav-group">
              <summary className={group.links.some(([path]) => pathname === path || pathname.startsWith(path + '/')) ? 'active' : ''}>
                {group.label}
              </summary>
              <div className="nav-menu">
                {group.links.map(([path, label]) => (
                  <NavLink
                    key={path}
                    to={path}
                    onClick={(e) => e.currentTarget.closest('details')?.removeAttribute('open')}
                    className={({ isActive }) => (isActive ? 'active' : '')}
                  >
                    {label}
                  </NavLink>
                ))}
              </div>
            </details>
          ))}
        </div>
        <NavLink to="/import" className={({ isActive }) => `nav-action${isActive ? ' active' : ''}`}>
          Add posting
        </NavLink>
        <button type="button" className="nav-logout" onClick={logout} title="Sign out of Career Navigator">Log out</button>
      </nav>
      <main id="main-content">
      <Routes>
        <Route path="/" element={<Root />} />
        <Route path="/future" element={<Explore />} />
        <Route path="/opportunities" element={<Dashboard />} />
        <Route path="/applications" element={<Applications />} />
        <Route path="/space" element={<Space />} />
        <Route path="/trends" element={<Trends />} />
        <Route path="/economics" element={<Economics />} />
        <Route path="/pathways" element={<Pathways />} />
        <Route path="/pathways/:id" element={<Pathways />} />
        <Route path="/targets" element={<Targets />} />
        <Route path="/targets/new" element={<AddTarget />} />
        <Route path="/targets/:id" element={<RoleDetail />} />
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
        <Route path="*" element={<div><h1>Page not found</h1><NavLink to="/">Return home</NavLink></div>} />
      </Routes>
      </main>
    </div>
  )
}

export default App
