// Primitive shimmer block. Use width/height as inline style or className.
export default function Skeleton({ width, height, style, className = '' }) {
  return (
    <span
      className={`skeleton ${className}`}
      style={{ width, height, display: 'block', ...style }}
    />
  );
}
