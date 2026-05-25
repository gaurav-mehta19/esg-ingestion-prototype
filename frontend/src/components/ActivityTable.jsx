// One table component for all sources. The unified shape works because
// ActivityRecord is unified — every row has date, scope, type, raw,
// normalized, status. Source-specific detail lives in the drawer.
//
// Visual treatment by row status:
//   flagged   → amber left border (analyst attention)
//   ingested  → light gray (queued)
//   approved  → green left border
//   locked    → green with lock icon (terminal)
//
// Reversal and estimated rows get inline badges so they don't need to be
// opened to be seen.

import {
  ReviewStatusBadge,
  ScopeBadge,
  ReversalBadge,
  SpendProxyBadge,
} from './StatusBadge.jsx';
import EmptyState from './EmptyState.jsx';
import { sourceTypeLabel } from '../lib/labels.js';

function rowClass(record) {
  if (record.review_status === 'locked') return 'row row-locked';
  if (record.review_status === 'approved') return 'row row-approved';
  if (record.review_status === 'flagged') return 'row row-flagged';
  if (record.review_status === 'analyst_reviewed') return 'row row-reviewed';
  return 'row row-ingested';
}

function formatQty(qty, unit) {
  if (qty === null || qty === undefined) return '—';
  const num = Number(qty);
  const display = Math.abs(num) >= 1 ? num.toLocaleString(undefined, { maximumFractionDigits: 2 }) : num;
  return `${display} ${unit || ''}`.trim();
}

export default function ActivityTable({ records, onSelect, emptyMessage }) {
  if (records.length === 0) {
    return <EmptyState title={emptyMessage || 'No records'} />;
  }
  return (
    <table className="activity-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Scope</th>
          <th>Activity</th>
          <th>Location</th>
          <th>As reported</th>
          <th>Normalized</th>
          <th>Status</th>
          <th>Why flagged</th>
        </tr>
      </thead>
      <tbody>
        {records.map((r) => (
          <tr key={r.id} className={rowClass(r)} onClick={() => onSelect(r.id)}>
            <td>{r.activity_date}</td>
            <td><ScopeBadge value={r.ghg_scope} /></td>
            <td>
              <div className="cell-primary">{sourceTypeLabel(r.source_type)}</div>
              <div className="cell-secondary">{r.activity_type}</div>
            </td>
            <td>{r.facility_name || r.location_country_code || '—'}</td>
            <td>{formatQty(r.raw_quantity, r.raw_unit)}</td>
            <td>{formatQty(r.normalized_quantity, r.normalized_unit)}</td>
            <td>
              <ReviewStatusBadge value={r.review_status} />
              <div className="row-badges">
                {r.is_reversal && <ReversalBadge />}
                {r.distance_computation_method === 'spend_proxy' && <SpendProxyBadge />}
              </div>
            </td>
            <td className="cell-reason">
              {r.flagged_reason && <span className="reason-inline">{r.flagged_reason}</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
