import { lazy, Suspense, useRef, useState } from 'react'
import RouteErrorBoundary from './components/RouteErrorBoundary'
import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './useAuth'
import { hasLegacyRoleListQuery } from './lib/roleNavigation'
import { isApplicationWorkflow } from './lib/workflowContext'
import Home from './pages/Home'
const Explore = lazy(() => import('./pages/Explore'))
const CareerDirectionBuilder = lazy(() => import('./pages/CareerDirectionBuilder'))
const CareerDirectionDetail = lazy(() => import('./pages/CareerDirectionDetail'))
const Applications = lazy(() => import('./pages/Applications'))
const ApplicationWorkspace = lazy(() => import('./pages/ApplicationWorkspace'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const RoleDetail = lazy(() => import('./pages/RoleDetail'))
const RoleEdit = lazy(() => import('./pages/RoleEdit'))
const RoleRequirements = lazy(() => import('./pages/RoleRequirements'))
const Import = lazy(() => import('./pages/Import'))
const Profile = lazy(() => import('./pages/Profile'))
const Profile360 = lazy(() => import('./pages/Profile360'))
const Comparison = lazy(() => import('./pages/Comparison'))
const Preferences = lazy(() => import('./pages/Preferences'))
const Space = lazy(() => import('./pages/Space'))
const Targets = lazy(() => import('./pages/Targets'))
const AddTarget = lazy(() => import('./pages/AddTarget'))
const Episodes = lazy(() => import('./pages/Episodes'))
const Vocabulary = lazy(() => import('./pages/Vocabulary'))
const Capabilities = lazy(() => import('./pages/Capabilities'))
const CapabilityCoverage = lazy(() => import('./pages/CapabilityCoverage'))
const Trends = lazy(() => import('./pages/Trends'))
const Economics = lazy(() => import('./pages/Economics'))
const Pathways = lazy(() => import('./pages/Pathways'))
const MarketCoverage = lazy(() => import('./pages/MarketCoverage'))

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
const PRIMARY_LINKS: { path: string; label: string; match: (pathname: string, search: string) => boolean }[] = [
  { path: '/', label: 'Home', match: (p) => p === '/' },
  {
    path: '/future',
    label: 'Explore my future',
    match: (p) => p === '/future' || p.startsWith('/future/') || p === '/targets' || p.startsWith('/targets/') || p === '/pathways' || p.startsWith('/pathways/'),
  },
  {
    path: '/opportunities',
    label: 'Opportunities',
    match: (p, s) => !isApplicationWorkflow(s) && (p === '/opportunities' || p.startsWith('/roles/') || p.startsWith('/comparison/') || p.startsWith('/role-instances/')),
  },
  { path: '/applications', label: 'Applications', match: (p, s) => p === '/applications' || p.startsWith('/applications/') || (isApplicationWorkflow(s) && (p === '/opportunities' || p.startsWith('/roles/') || p.startsWith('/comparison/') || p.startsWith('/role-instances/'))) },
]

function App() {
  const { logout } = useAuth()
  const { pathname, search } = useLocation()
  const [toolsOpen, setToolsOpen] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)
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
        ['/market/coverage', 'Market coverage'],
      ],
    },
  ]

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="site-header">
        <NavLink to="/" className="brand" aria-label="Career Navigator home"><span className="brand-mark" aria-hidden="true">↗</span><span>Career Navigator<small>Your next chapter</small></span></NavLink>
        <button ref={menuButton} className="tools-toggle" aria-expanded={toolsOpen} aria-controls="workspace-tools" onClick={() => setToolsOpen(v => !v)} onKeyDown={e => { if (e.key === 'Escape') setToolsOpen(false) }}>Menu</button>
      </header>
      <nav className="nav" aria-label="Main navigation" onClick={e => { if ((e.target as HTMLElement).closest('a')) setToolsOpen(false) }} onKeyDown={e => { if (e.key === 'Escape') { setToolsOpen(false); menuButton.current?.focus() } }}>
        <div className="nav-primary">
          {PRIMARY_LINKS.map(({ path, label, match }) => (
            <NavLink key={path} to={path} end={path === '/'} aria-current={match(pathname, search) ? 'page' : undefined} className={() => (match(pathname, search) ? 'active' : '')}>
              {label}
            </NavLink>
          ))}
        </div>
        <div id="workspace-tools" className={`workspace-tools${toolsOpen ? ' is-open' : ''}`}><p className="nav-caption">Your workspace</p><div className="nav-secondary">
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
      </div></nav>
      <main id="main-content" className="workspace-main" tabIndex={-1}>
      <RouteErrorBoundary key={pathname}><Suspense fallback={<div role="status" className="page-skeleton">Loading your workspace…<span /><span /><span /></div>}>
      <Routes>
        <Route path="/" element={<Root />} />
        <Route path="/future" element={<Explore />} />
        <Route path="/future/build" element={<CareerDirectionBuilder />} />
        <Route path="/future/directions/:id" element={<CareerDirectionDetail />} />
        <Route path="/opportunities" element={<Dashboard />} />
        <Route path="/applications" element={<Applications />} />
        <Route path="/applications/:id" element={<ApplicationWorkspace />} />
        <Route path="/space" element={<Space />} />
        <Route path="/trends" element={<Trends />} />
        <Route path="/economics" element={<Economics />} />
        <Route path="/market/coverage" element={<MarketCoverage />} />
        <Route path="/pathways" element={<Pathways />} />
        <Route path="/pathways/:id" element={<Pathways />} />
        <Route path="/targets" element={<Targets />} />
        <Route path="/targets/new" element={<AddTarget />} />
        <Route path="/targets/:id" element={<RoleDetail />} />
        <Route path="/targets/:id/edit" element={<RoleEdit />} />
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
      </Suspense></RouteErrorBoundary>
      </main>
    </div>
  )
}

export default App
