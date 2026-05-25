// One badge component, three variant maps. Reused everywhere a status is shown.
// Variants share visual rules: failure red, warning amber, success green, neutral gray.

import {
  reviewStatusLabel,
  scopeLabel,
  stagingStatusLabel,
} from '../lib/labels.js';

const REVIEW_VARIANT = {
  ingested: 'neutral',
  flagged: 'warning',
  analyst_reviewed: 'info',
  approved: 'success',
  locked: 'dark',
};

const STAGING_VARIANT = {
  rejected: 'danger',
  needs_uom_mapping: 'warning',
  needs_plant_mapping: 'warning',
  needs_meter_mapping: 'warning',
  needs_expense_type_mapping: 'warning',
  no_itinerary_linkage: 'info',
  duplicate: 'neutral',
};

const SCOPE_VARIANT = {
  scope_1: 'danger',
  scope_2: 'warning',
  scope_3: 'info',
};

function Badge({ variant, label, prefix }) {
  return (
    <span className={`badge badge-${variant}`}>
      {prefix && <span className="badge-prefix">{prefix}</span>}
      {label}
    </span>
  );
}

export function ReviewStatusBadge({ value }) {
  return <Badge variant={REVIEW_VARIANT[value] || 'neutral'} label={reviewStatusLabel(value)} />;
}

export function StagingStatusBadge({ value }) {
  return <Badge variant={STAGING_VARIANT[value] || 'neutral'} label={stagingStatusLabel(value)} />;
}

export function ScopeBadge({ value }) {
  return <Badge variant={SCOPE_VARIANT[value] || 'neutral'} label={scopeLabel(value)} />;
}

export function ReversalBadge() {
  return <Badge variant="warning" label="Reversal" prefix="↺" />;
}

export function EstimatedBadge() {
  return <Badge variant="warning" label="Estimated read" prefix="≈" />;
}

export function SpendProxyBadge() {
  return <Badge variant="info" label="Spend-based estimate" prefix="$" />;
}
