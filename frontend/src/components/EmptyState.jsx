// Empty state pattern for tables and lists. A small icon + a short message —
// less hostile than a single line of italic gray text.

export default function EmptyState({ icon = '∅', title, description }) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon" aria-hidden>{icon}</div>
      {title && <div className="empty-state-title">{title}</div>}
      {description && <div className="empty-state-description">{description}</div>}
    </div>
  );
}
