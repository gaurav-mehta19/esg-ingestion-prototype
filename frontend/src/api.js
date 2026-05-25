// Thin fetch wrapper. Vite dev server proxies /api → http://localhost:8000.

const ANALYST_ID = 'demo-analyst';

async function request(path, opts = {}) {
  const res = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      'X-Analyst-Id': ANALYST_ID,
      ...(opts.headers || {}),
    },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json();
}

export const api = {
  listOrganizations: () => request('/api/organizations/'),
  listActivityRecords: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/api/activity-records/?${qs}`);
  },
  getActivityRecord: (id) => request(`/api/activity-records/${id}/`),
  listSapStagingIssues: (organizationId) =>
    request(`/api/staging/sap/issues/?organization=${organizationId}`),
  listUtilityStagingIssues: (organizationId) =>
    request(`/api/staging/utility/issues/?organization=${organizationId}`),
  listTravelStagingIssues: (organizationId) =>
    request(`/api/staging/travel/issues/?organization=${organizationId}`),
  listIngestionRuns: (organizationId) =>
    request(`/api/ingestion-runs/?organization=${organizationId}`),

  flagRecord: (id, note) =>
    request(`/api/activity-records/${id}/flag/`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    }),
  markReviewed: (id) =>
    request(`/api/activity-records/${id}/mark_reviewed/`, { method: 'POST' }),
  approveRecord: (id, note = '') =>
    request(`/api/activity-records/${id}/approve/`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    }),
  lockRecord: (id) =>
    request(`/api/activity-records/${id}/lock/`, { method: 'POST' }),
};
