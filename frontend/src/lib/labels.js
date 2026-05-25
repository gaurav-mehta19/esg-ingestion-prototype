// One place to translate engineer-vocab to analyst-vocab. If the backend ever
// changes a code, this is the only frontend file that needs to follow.

export const SCOPE_LABEL = {
  scope_1: 'Scope 1 — Direct',
  scope_2: 'Scope 2 — Electricity',
  scope_3: 'Scope 3 — Value chain',
};

export const REVIEW_STATUS_LABEL = {
  ingested: 'Awaiting review',
  flagged: 'Needs attention',
  analyst_reviewed: 'Reviewed',
  approved: 'Approved',
  locked: 'Locked for audit',
};

export const SOURCE_TYPE_LABEL = {
  sap_goods_movement: 'SAP — Goods movement',
  utility_interval: 'Electricity meter',
  travel_flight: 'Business flight',
  travel_hotel: 'Hotel stay',
  travel_car_rental: 'Car rental',
  travel_personal_mileage: 'Personal mileage',
  travel_taxi: 'Taxi / rideshare',
  travel_rail: 'Rail travel',
};

export const SOURCE_GROUP_LABEL = {
  sap: 'SAP fuel & procurement',
  utility: 'Utility electricity',
  travel: 'Corporate travel',
};

export const STAGING_STATUS_LABEL = {
  rejected: 'Rejected',
  needs_uom_mapping: 'Needs unit setup',
  needs_plant_mapping: 'Needs plant setup',
  needs_meter_mapping: 'Needs meter setup',
  needs_expense_type_mapping: 'Needs expense type setup',
  no_itinerary_linkage: 'No itinerary linked',
  duplicate: 'Duplicate',
};

// Plain-English next-step guidance for stuck staging rows. Each one ends
// where the analyst's hands actually need to go.
export const STAGING_ACTION_HINT = {
  rejected:
    'The source data is malformed and cannot be recovered. Send the raw row back to the data provider for correction, or delete from staging.',
  needs_uom_mapping:
    'Add a conversion rule in Django Admin → UOM Normalization Rules for the unit shown, then re-run normalization for this source.',
  needs_plant_mapping:
    'Add a mapping in Django Admin → Plant Location Mappings for the SAP plant code shown, then re-run normalization.',
  needs_meter_mapping:
    'Add a mapping in Django Admin → Meter Location Mappings for the meter ID shown, then re-run normalization.',
  needs_expense_type_mapping:
    'Add a mapping in Django Admin → Expense Type Category Mappings for the code shown, then re-run normalization.',
  no_itinerary_linkage:
    'No fix required — the flight was booked outside Concur. Emissions will use a spend-based estimate (lower accuracy). Confirm and approve when ready.',
  duplicate:
    'Already ingested in a prior run. Safe to ignore.',
};

// Map a source_type on ActivityRecord → which source-group tab it belongs to.
export function sourceGroupForRecord(sourceType) {
  if (!sourceType) return 'sap';
  if (sourceType.startsWith('sap')) return 'sap';
  if (sourceType.startsWith('utility')) return 'utility';
  if (sourceType.startsWith('travel')) return 'travel';
  return 'sap';
}

export function reviewStatusLabel(value) {
  return REVIEW_STATUS_LABEL[value] || value;
}
export function scopeLabel(value) {
  return SCOPE_LABEL[value] || value;
}
export function sourceTypeLabel(value) {
  return SOURCE_TYPE_LABEL[value] || value;
}
export function stagingStatusLabel(value) {
  return STAGING_STATUS_LABEL[value] || value;
}
export function stagingActionHint(value) {
  return STAGING_ACTION_HINT[value] || 'Review the reason and decide whether to retry or escalate.';
}
