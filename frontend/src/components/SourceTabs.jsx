// Source-first navigation. Each tab shows a small count badge so the analyst
// sees at a glance "where the work is."
//
// Analysts typically own one source at a time during a review cycle, so making
// source the primary navigation reduces context-switching.

import { SOURCE_GROUP_LABEL } from '../lib/labels.js';

const SOURCES = ['all', 'sap', 'utility', 'travel'];

export default function SourceTabs({ active, onChange, countsBySource }) {
  return (
    <nav className="source-tabs" aria-label="Data source">
      {SOURCES.map((key) => {
        const c = countsBySource[key] || { total: 0, attention: 0 };
        const label = key === 'all' ? 'All sources' : SOURCE_GROUP_LABEL[key];
        return (
          <button
            key={key}
            className={`source-tab ${active === key ? 'is-active' : ''}`}
            onClick={() => onChange(key)}
          >
            <span className="source-tab-label">{label}</span>
            <span className="source-tab-count">
              {c.total}
              {c.attention > 0 && (
                <span className="source-tab-attention" title="Items needing attention">
                  {' '}· {c.attention} ⚠
                </span>
              )}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
