// Top-level dashboard. Source-first navigation. Panels rendered via <Card/>.

import { useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import KpiStrip from '../components/KpiStrip.jsx';
import SourceTabs from '../components/SourceTabs.jsx';
import ActivityTable from '../components/ActivityTable.jsx';
import StagingIssueCard from '../components/StagingIssueCard.jsx';
import RecordDetailDrawer from '../components/RecordDetailDrawer.jsx';
import Card from '../components/Card.jsx';
import EmptyState from '../components/EmptyState.jsx';
import { sourceGroupForRecord, SOURCE_GROUP_LABEL } from '../lib/labels.js';

export default function ReviewDashboard({ organization }) {
  const [records, setRecords] = useState([]);
  const [issuesBySource, setIssuesBySource] = useState({ sap: [], utility: [], travel: [] });
  const [selectedId, setSelectedId] = useState(null);
  const [activeTab, setActiveTab] = useState('all');
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!organization) return;
    api.listActivityRecords({ organization: organization.id, limit: 500 })
      .then((res) => setRecords(res.results || res));
    Promise.all([
      api.listSapStagingIssues(organization.id),
      api.listUtilityStagingIssues(organization.id),
      api.listTravelStagingIssues(organization.id),
    ]).then(([sap, util, travel]) => setIssuesBySource({
      sap: sap.results || sap,
      utility: util.results || util,
      travel: travel.results || travel,
    }));
  }, [organization, tick]);

  const allIssues = useMemo(
    () => [...issuesBySource.sap, ...issuesBySource.utility, ...issuesBySource.travel],
    [issuesBySource],
  );

  const recordsBySource = useMemo(() => {
    const groups = { all: records, sap: [], utility: [], travel: [] };
    for (const r of records) {
      const g = sourceGroupForRecord(r.source_type);
      groups[g].push(r);
    }
    return groups;
  }, [records]);

  const countsBySource = useMemo(() => {
    function attentionCount(rs, is) {
      const r = rs.filter((x) => x.review_status === 'flagged' || x.review_status === 'ingested').length;
      return r + is.length;
    }
    return {
      all: { total: records.length + allIssues.length, attention: attentionCount(records, allIssues) },
      sap: { total: recordsBySource.sap.length + issuesBySource.sap.length, attention: attentionCount(recordsBySource.sap, issuesBySource.sap) },
      utility: { total: recordsBySource.utility.length + issuesBySource.utility.length, attention: attentionCount(recordsBySource.utility, issuesBySource.utility) },
      travel: { total: recordsBySource.travel.length + issuesBySource.travel.length, attention: attentionCount(recordsBySource.travel, issuesBySource.travel) },
    };
  }, [records, allIssues, recordsBySource, issuesBySource]);

  const visibleRecords = activeTab === 'all' ? records : recordsBySource[activeTab];
  const visibleIssues = activeTab === 'all' ? allIssues : issuesBySource[activeTab];

  const needsAttention = visibleRecords.filter((r) => r.review_status === 'ingested' || r.review_status === 'flagged');
  const reviewed = visibleRecords.filter((r) => r.review_status === 'analyst_reviewed' || r.review_status === 'approved');
  const locked = visibleRecords.filter((r) => r.review_status === 'locked');

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Review</h1>
          <p className="page-sub">
            Ingested data from {Object.values(countsBySource).filter((c) => c.total > 0).length} {Object.values(countsBySource).filter((c) => c.total > 0).length === 1 ? 'source' : 'sources'} —
            click a row to inspect and approve.
          </p>
        </div>
      </div>

      <KpiStrip records={records} allIssues={allIssues} />

      <SourceTabs active={activeTab} onChange={setActiveTab} countsBySource={countsBySource} />

      {visibleIssues.length > 0 && (
        <Card
          tone="warning"
          title={`Failed to ingest or normalize — ${visibleIssues.length}`}
          subtitle="Each row tells you what to fix. Apply the suggested change in Django admin, then re-run normalization for that source."
        >
          <div className="issue-grid">
            {visibleIssues.map((iss) => (
              <StagingIssueCard key={iss.id} issue={iss} />
            ))}
          </div>
        </Card>
      )}

      <Card
        title={`Awaiting your review — ${needsAttention.length}`}
        subtitle="Activity records the platform successfully created. Click any row to inspect the numbers and approve."
      >
        {needsAttention.length === 0 ? (
          <EmptyState
            icon="✓"
            title="Nothing waiting for review"
            description={activeTab === 'all' ? 'All sources are clean.' : `No pending records in ${SOURCE_GROUP_LABEL[activeTab] || activeTab}.`}
          />
        ) : (
          <ActivityTable records={needsAttention} onSelect={setSelectedId} />
        )}
      </Card>

      <Card
        title={`Reviewed &amp; approved — ${reviewed.length}`}
        subtitle="Approved by an analyst. Still editable until locked."
      >
        {reviewed.length === 0 ? (
          <EmptyState icon="·" title="No approved records yet" description="Records you approve will appear here." />
        ) : (
          <ActivityTable records={reviewed} onSelect={setSelectedId} />
        )}
      </Card>

      <Card
        title={`Locked for audit — ${locked.length}`}
        subtitle="Immutable. Will appear in external reports as-is."
      >
        {locked.length === 0 ? (
          <EmptyState icon="🔒" title="Nothing locked yet" description="Approved records can be locked when audit-ready." />
        ) : (
          <ActivityTable records={locked} onSelect={setSelectedId} />
        )}
      </Card>

      <RecordDetailDrawer
        recordId={selectedId}
        onClose={() => setSelectedId(null)}
        onChanged={() => setTick((t) => t + 1)}
      />
    </>
  );
}
