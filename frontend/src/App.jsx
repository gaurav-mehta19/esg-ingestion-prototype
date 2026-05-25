import { useEffect, useState } from 'react';
import { api } from './api.js';
import ReviewDashboard from './pages/ReviewDashboard.jsx';
import Header from './components/Header.jsx';
import EmptyState from './components/EmptyState.jsx';
import { ToastProvider } from './components/Toast.jsx';

export default function App() {
  const [orgs, setOrgs] = useState([]);
  const [selectedOrg, setSelectedOrg] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listOrganizations().then((res) => {
      const items = res.results || res;
      setOrgs(items);
      if (items.length > 0) setSelectedOrg(items[0]);
      setLoading(false);
    }).catch((e) => {
      console.error('Failed to load orgs', e);
      setLoading(false);
    });
  }, []);

  return (
    <ToastProvider>
      <Header orgs={orgs} selectedOrg={selectedOrg} onSelectOrg={setSelectedOrg} />
      <main className="app-main">
        {loading ? null : selectedOrg ? (
          <ReviewDashboard organization={selectedOrg} />
        ) : (
          <EmptyState
            icon="○"
            title="No organizations found"
            description={
              <>Run <code>python manage.py seed_sap_demo</code> in <code>backend/</code> to create the demo organization.</>
            }
          />
        )}
      </main>
    </ToastProvider>
  );
}
