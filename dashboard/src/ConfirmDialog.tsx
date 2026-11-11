import { useEffect, useRef, type ReactNode } from "react";

/** The dashboard's own dialog, for a last look before an action that changes things. */
export function ConfirmDialog({
  title,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
  children,
}: Readonly<{
  title: string;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children: ReactNode;
}>) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element || element.open) return;
    if (typeof element.showModal === "function") element.showModal();
    else element.setAttribute("open", "");
  }, []);

  return (
    <dialog
      ref={dialog}
      className="confirm"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
    >
      <h2>{title}</h2>
      {children}
      <div className="confirm-actions">
        <button type="button" className="secondary" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="primary"
          disabled={busy}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
