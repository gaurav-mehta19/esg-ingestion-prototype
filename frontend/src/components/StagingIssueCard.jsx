// Each stuck row is shown as a CARD (not a table row) because each one needs a
// specific human action — and cards have room to spell out what that action is.
// The card layout pulls the eye to: what came in, why it's stuck, what to do.

import { stagingActionHint } from '../lib/labels.js';
import { StagingStatusBadge } from './StatusBadge.jsx';

export default function StagingIssueCard({ issue }) {
  const isFatal = issue.staging_status === 'rejected';
  return (
    <div className={`issue-card issue-${isFatal ? 'fatal' : 'fixable'}`}>
      <div className="issue-card-head">
        <StagingStatusBadge value={issue.staging_status} />
        <span className="issue-card-source">{issue.source_label}</span>
      </div>

      <div className="issue-card-reason">
        <strong>Why it's stuck:</strong> {issue.rejection_reason || '(no detail provided)'}
      </div>

      <div className="issue-card-action">
        <strong>Next step:</strong> {stagingActionHint(issue.staging_status)}
      </div>

      {Array.isArray(issue.parse_warnings) && issue.parse_warnings.length > 0 && (
        <details className="issue-card-warnings">
          <summary>Parser warnings ({issue.parse_warnings.length})</summary>
          <ul>
            {issue.parse_warnings.map((w, i) => (
              <li key={i}>
                <code>{w.field}</code>: {w.warning}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
