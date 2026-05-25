// One card component for panels, sections, and modular blocks.
// Has an optional header (title + subtitle + actions) and a body.
//
// Why one component for both top-level panels and sub-blocks:
//   The "panel" pattern repeated 4+ times in the dashboard and the section
//   pattern in the drawer have the same shape — title + supporting text +
//   content. Consolidating into one component made the dashboard JSX
//   significantly cleaner and the spacing consistent.

export default function Card({
  title,
  subtitle,
  actions,
  tone = 'default', // 'default' | 'warning' | 'danger' | 'success'
  padding = 'md',   // 'sm' | 'md' | 'lg'
  children,
  className = '',
}) {
  return (
    <section className={`card card-tone-${tone} card-pad-${padding} ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div className="card-head-text">
            {title && <h3 className="card-title">{title}</h3>}
            {subtitle && <p className="card-subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="card-head-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}
