// Detail drawer for one ActivityRecord. Three sections:
//   1. The numbers — raw vs normalized side by side (regulator's first question)
//   2. Provenance — where did this come from, how was it derived
//   3. Actions — explicit confirm for state changes
//   4. Audit trail — every event that touched this record, with author
//
// The drawer is intentionally read-heavy: explanation first, action second.
// Mistakes here can lock bad data into the audit trail.

import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { scopeLabel, sourceTypeLabel } from '../lib/labels.js';
import {
  EstimatedBadge,
  ReversalBadge,
  ReviewStatusBadge,
  ScopeBadge,
  SpendProxyBadge,
} from './StatusBadge.jsx';
import ConfirmDialog from './ConfirmDialog.jsx';
import Button from './Button.jsx';
import { useToast } from './Toast.jsx';

const VERB_SUCCESS = {
  flag: 'Record flagged',
  review: 'Marked as reviewed',
  approve: 'Record approved',
  lock: 'Record locked for audit',
};

export default function RecordDetailDrawer({ recordId, onClose, onChanged }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [pendingAction, setPendingAction] = useState(null); // 'flag' | 'approve' | 'lock' | null
  const toast = useToast();

  useEffect(() => {
    if (!recordId) return;
    setDetail(null);
    setError(null);
    api.getActivityRecord(recordId).then(setDetail).catch((e) => setError(e.message));
  }, [recordId]);

  if (!recordId) return null;

  function reload() {
    api.getActivityRecord(recordId).then(setDetail);
    onChanged();
  }

  async function runTransition(verb, note = '') {
    try {
      if (verb === 'flag') await api.flagRecord(recordId, note);
      if (verb === 'review') await api.markReviewed(recordId);
      if (verb === 'approve') await api.approveRecord(recordId, note);
      if (verb === 'lock') await api.lockRecord(recordId);
      setPendingAction(null);
      toast.success(VERB_SUCCESS[verb] || 'Done');
      reload();
    } catch (e) {
      toast.error(`Could not ${verb}: ${e.message}`);
      setPendingAction(null);
    }
  }

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Activity record detail">
        <header className="drawer-head">
          <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
          <h2>Activity record</h2>
          {detail && (
            <div className="drawer-head-meta">
              <ReviewStatusBadge value={detail.review_status} />
              {detail.is_reversal && <ReversalBadge />}
              {detail.distance_computation_method === 'spend_proxy' && <SpendProxyBadge />}
            </div>
          )}
        </header>

        {error && <p className="drawer-error">Could not load: {error}</p>}
        {!detail && !error && <p className="drawer-loading">Loading…</p>}

        {detail && (
          <>
            <DrawerActions
              detail={detail}
              onAction={setPendingAction}
            />

            <Section title="What it is">
              <KV label="Activity" value={`${sourceTypeLabel(detail.source_type)} — ${detail.activity_type}`} />
              <KV label="Scope" value={<ScopeBadge value={detail.ghg_scope} />} />
              <KV label="Category" value={detail.ghg_category || '—'} />
              <KV label="Date" value={detail.activity_date} />
              {detail.activity_period_start && (
                <KV label="Period" value={`${detail.activity_period_start} → ${detail.activity_period_end || '?'}`} />
              )}
              <KV label="Location" value={`${detail.facility_name || '—'} (${detail.location_country_code || '?'})`} />
              {detail.grid_region && <KV label="Grid region" value={detail.grid_region} />}
            </Section>

            <Section title="The numbers">
              <div className="numbers-grid">
                <div className="numbers-side">
                  <div className="numbers-label">As reported by source</div>
                  <div className="numbers-value">{detail.raw_quantity} <span className="unit">{detail.raw_unit}</span></div>
                </div>
                <div className="numbers-arrow">→</div>
                <div className="numbers-side">
                  <div className="numbers-label">After normalization</div>
                  <div className="numbers-value">{detail.normalized_quantity ?? '—'} <span className="unit">{detail.normalized_unit}</span></div>
                </div>
              </div>
              {detail.unit_conversion_factor && Number(detail.unit_conversion_factor) !== 1 && (
                <p className="numbers-explanation">
                  Conversion: raw × <strong>{Number(detail.unit_conversion_factor).toLocaleString(undefined, { maximumFractionDigits: 6 })}</strong>
                  {detail.unit_conversion_notes && <> — {detail.unit_conversion_notes}</>}
                </p>
              )}
              {detail.computed_distance_km != null && (
                <p className="numbers-explanation">
                  Distance derived: <strong>{Number(detail.computed_distance_km).toLocaleString()} km</strong> via {detail.distance_computation_method || 'unknown method'}
                </p>
              )}
              <div className="missing-factor-note">
                Emission factor: <strong>not yet calculated</strong>. The factor lookup
                runs in the next pipeline phase (see MODEL.md §9). Approval here certifies
                the activity data; emissions are computed downstream.
              </div>
            </Section>

            <Section title="Where it came from">
              <KV label="Source system" value={detail.source_type} mono />
              <KV label="Staging row" value={<code className="mono small">{detail.staging_id}</code>} />
              <KV label="Ingestion run" value={<code className="mono small">{detail.ingestion_run}</code>} />
              <KV label="Notes" value={detail.staging_notes || '—'} multiline />
            </Section>

            {detail.flagged_reason && (
              <Section title="Why this row is flagged" tone="warning">
                <p className="flag-explainer">{detail.flagged_reason}</p>
              </Section>
            )}

            <Section title="Audit trail">
              <AuditTrail events={detail.review_events || []} />
            </Section>
          </>
        )}
      </aside>

      {pendingAction === 'flag' && (
        <ConfirmDialog
          title="Flag this record"
          body="Flag records you want another analyst to take a second look at, or that you want to come back to. The reason is recorded in the audit trail."
          confirmLabel="Flag with reason"
          tone="warning"
          noteRequired
          noteLabel="Reason for flagging"
          onConfirm={(note) => runTransition('flag', note)}
          onCancel={() => setPendingAction(null)}
        />
      )}
      {pendingAction === 'approve' && (
        <ConfirmDialog
          title="Approve this record for audit?"
          body={
            <>
              <p>This certifies the activity data as correct. The record can still be unflagged or edited until it is locked.</p>
              <p><strong>Your name and the timestamp will be permanently recorded in the audit trail.</strong></p>
            </>
          }
          confirmLabel="Approve"
          tone="primary"
          onConfirm={(note) => runTransition('approve', note)}
          onCancel={() => setPendingAction(null)}
        />
      )}
      {pendingAction === 'lock' && (
        <ConfirmDialog
          title="Lock for audit?"
          body={
            <>
              <p><strong>This cannot be undone.</strong> Locked records cannot be edited, flagged, or have their status changed. They become part of the immutable audit record for external reporting.</p>
              <p>Only lock when you are certain the record is final.</p>
            </>
          }
          confirmLabel="Lock permanently"
          tone="danger"
          noteRequired
          noteLabel="Confirm reason for locking"
          onConfirm={() => runTransition('lock')}
          onCancel={() => setPendingAction(null)}
        />
      )}
    </>
  );
}

function DrawerActions({ detail, onAction }) {
  const status = detail.review_status;
  return (
    <div className="drawer-actions">
      {status !== 'locked' && (
        <Button variant="ghost" onClick={() => onAction('flag')}>Flag for follow-up</Button>
      )}
      {(status === 'ingested' || status === 'flagged') && (
        <Button variant="secondary" onClick={() => onAction('approve')}>Mark reviewed &amp; approve</Button>
      )}
      {status === 'analyst_reviewed' && (
        <Button variant="primary" onClick={() => onAction('approve')}>Approve</Button>
      )}
      {status === 'approved' && (
        <Button variant="danger" onClick={() => onAction('lock')}>Lock for audit</Button>
      )}
      {status === 'locked' && (
        <span className="locked-notice">🔒 This record is locked and cannot be changed.</span>
      )}
    </div>
  );
}

function Section({ title, children, tone }) {
  return (
    <section className={`drawer-section ${tone ? `drawer-section-${tone}` : ''}`}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function KV({ label, value, mono, multiline }) {
  return (
    <div className="kv">
      <div className="kv-label">{label}</div>
      <div className={`kv-value ${mono ? 'mono' : ''} ${multiline ? 'multiline' : ''}`}>{value}</div>
    </div>
  );
}

function AuditTrail({ events }) {
  if (events.length === 0) return <p className="empty">No events.</p>;
  return (
    <ol className="audit-trail">
      {events.map((e) => (
        <li key={e.id} className={`audit-event audit-${e.event_type}`}>
          <div className="audit-event-head">
            <strong>{eventVerb(e.event_type)}</strong>
            {e.from_status && e.to_status && (
              <span className="audit-transition">
                {' '}{e.from_status} → {e.to_status}
              </span>
            )}
          </div>
          <div className="audit-event-meta">
            by <strong>{e.actor_display_name || e.actor_id}</strong> · {new Date(e.occurred_at).toLocaleString()}
          </div>
          {e.note && <div className="audit-event-note">{e.note}</div>}
        </li>
      ))}
    </ol>
  );
}

function eventVerb(t) {
  return {
    ingested: 'Ingested',
    auto_flagged: 'Auto-flagged by system',
    manually_flagged: 'Flagged by analyst',
    unflagged: 'Unflagged',
    analyst_reviewed: 'Marked reviewed',
    approved: 'Approved',
    rejected: 'Rejected',
    locked: 'Locked for audit',
    unlocked: 'Unlocked',
    field_edited: 'Edited',
    calculation_rerun: 'Recalculated',
  }[t] || t;
}
