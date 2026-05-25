// Consolidated button. One component for all variants; size and tone via props.
// Replaces the half-dozen `<button className="btn btn-primary">` patterns.

import Spinner from './Spinner.jsx';

export default function Button({
  variant = 'secondary', // 'primary' | 'secondary' | 'ghost' | 'danger' | 'warning'
  size = 'md',           // 'sm' | 'md'
  loading = false,
  disabled = false,
  icon = null,
  children,
  ...rest
}) {
  return (
    <button
      className={`btn btn-${variant} btn-${size} ${loading ? 'btn-loading' : ''}`}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner size="xs" /> : icon && <span className="btn-icon">{icon}</span>}
      <span className="btn-label">{children}</span>
    </button>
  );
}
