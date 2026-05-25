// CSS-only spinner. Used inside buttons and for in-flight actions.

export default function Spinner({ size = 'md' }) {
  return <span className={`spinner spinner-${size}`} role="status" aria-label="Loading" />;
}
