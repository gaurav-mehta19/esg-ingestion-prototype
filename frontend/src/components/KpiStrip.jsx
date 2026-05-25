// Top-of-page numbers. Designed to answer: "what does the analyst need to act
// on right now?" Big numbers, plain English, the actionable ones colored.

export default function KpiStrip({ records, allIssues }) {
  const counts = records.reduce(
    (acc, r) => {
      acc[r.review_status] = (acc[r.review_status] || 0) + 1;
      return acc;
    },
    { ingested: 0, flagged: 0, analyst_reviewed: 0, approved: 0, locked: 0 },
  );

  const needsAttention = counts.ingested + counts.flagged;
  const rejected = allIssues.filter((i) => i.staging_status === 'rejected').length;
  const stuck = allIssues.length - rejected;
  const lockedForAudit = counts.locked;
  const approvedReady = counts.approved + counts.analyst_reviewed;

  return (
    <div className="kpi-strip">
      <Kpi label="Needs attention" value={needsAttention} tone={needsAttention > 0 ? 'warning' : 'neutral'} hint="Activity records awaiting analyst review" />
      <Kpi label="Failed at ingest" value={rejected} tone={rejected > 0 ? 'danger' : 'neutral'} hint="Source rows the platform could not read" />
      <Kpi label="Awaiting setup" value={stuck} tone={stuck > 0 ? 'warning' : 'neutral'} hint="Missing UOM rule, plant/meter mapping, or expense type mapping" />
      <Kpi label="Ready to lock" value={approvedReady} tone="info" hint="Reviewed/approved records — lock when audit-ready" />
      <Kpi label="Locked for audit" value={lockedForAudit} tone="success" hint="Immutable; ready for external reporting" />
    </div>
  );
}

function Kpi({ label, value, tone, hint }) {
  return (
    <div className={`kpi kpi-${tone}`} title={hint}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-hint">{hint}</div>
    </div>
  );
}
