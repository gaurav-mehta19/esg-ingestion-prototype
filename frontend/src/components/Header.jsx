// Top-of-app bar. Brand mark + product name on the left, org picker on the right.
// Sticky so the org selector stays visible while scrolling long record lists.

export default function Header({ orgs, selectedOrg, onSelectOrg }) {
  return (
    <header className="app-header">
      <div className="app-header-inner">
        <div className="brand">
          <div className="brand-mark" aria-hidden>◉</div>
          <div className="brand-text">
            <div className="brand-name">ESG Platform</div>
            <div className="brand-tag">Review &amp; audit dashboard</div>
          </div>
        </div>

        {orgs.length > 0 && (
          <div className="org-switcher">
            <label htmlFor="org-select">Organization</label>
            <select
              id="org-select"
              value={selectedOrg?.id || ''}
              onChange={(e) => onSelectOrg(orgs.find((o) => o.id === e.target.value))}
            >
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>{o.name}</option>
              ))}
            </select>
          </div>
        )}
      </div>
    </header>
  );
}
