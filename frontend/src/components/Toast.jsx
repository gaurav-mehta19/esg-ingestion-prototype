// Minimal toast system. No external dependency.
//
// Usage:
//   const toast = useToast();
//   toast.success('Approved');
//   toast.error('Could not approve: …');
//   toast.info('Re-running normalization…');
//
// Toasts auto-dismiss after 4s; errors stay 8s so they're readable.
// Click to dismiss early.

import { createContext, useCallback, useContext, useEffect, useState } from 'react';

const ToastContext = createContext(null);

let _nextId = 1;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((tone, message) => {
    const id = _nextId++;
    setToasts((prev) => [...prev, { id, tone, message }]);
    const ttl = tone === 'error' ? 8000 : 4000;
    setTimeout(() => dismiss(id), ttl);
  }, [dismiss]);

  const api = {
    success: (msg) => push('success', msg),
    error: (msg) => push('error', msg),
    info: (msg) => push('info', msg),
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>');
  return ctx;
}

function ToastContainer({ toasts, onDismiss }) {
  return (
    <div className="toast-container" aria-live="polite" aria-atomic="false">
      {toasts.map((t) => (
        <Toast key={t.id} toast={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

function Toast({ toast, onDismiss }) {
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    const ttl = toast.tone === 'error' ? 7700 : 3700;
    const t = setTimeout(() => setLeaving(true), ttl);
    return () => clearTimeout(t);
  }, [toast.tone]);

  return (
    <button
      className={`toast toast-${toast.tone} ${leaving ? 'toast-leaving' : ''}`}
      onClick={onDismiss}
      aria-label={`Dismiss ${toast.tone}`}
    >
      <span className="toast-icon">{iconFor(toast.tone)}</span>
      <span className="toast-message">{toast.message}</span>
      <span className="toast-close">×</span>
    </button>
  );
}

function iconFor(tone) {
  if (tone === 'success') return '✓';
  if (tone === 'error') return '!';
  return 'ⓘ';
}
