// Modal confirmation. Approve and Lock both go through here — "explicit"
// means the analyst stops and clicks twice, with the consequence spelled out.

import { useState } from 'react';
import Button from './Button.jsx';

export default function ConfirmDialog({
  title,
  body,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'primary',
  noteRequired = false,
  noteLabel = 'Reason',
  onConfirm,
  onCancel,
}) {
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function handleConfirm(e) {
    if (e) e.preventDefault();
    if (noteRequired && !note.trim()) return;
    setSubmitting(true);
    try {
      await onConfirm(note);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <h3 className="modal-title">{title}</h3>
        <div className="modal-body">{body}</div>
        {noteRequired && (
          <label className="modal-note-label">
            {noteLabel}
            <textarea
              autoFocus
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Required — explain the reason for the audit trail"
            />
          </label>
        )}
        <div className="modal-actions">
          <Button variant="ghost" onClick={onCancel}>{cancelLabel}</Button>
          <Button
            variant={tone}
            onClick={handleConfirm}
            disabled={noteRequired && !note.trim()}
            loading={submitting}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
