// A modal dialog: focus goes in and comes back, Tab stays inside (dialogFocus.ts), Escape and a
// backdrop click close it.

import { useEffect, useId, useRef, type ReactNode } from "react";
import { useDialogFocus } from "./dialogFocus";
import { isTopmostModal, MODAL_SELECTOR } from "./escape";
import { Icon } from "./icons";

interface ModalProps {
  title: string;
  onClose: () => void;
  /** While true, Escape and the backdrop do nothing (a request is in flight). */
  busy?: boolean;
  wide?: boolean;
  children: ReactNode;
}

export function Modal({ title, onClose, busy = false, wide = false, children }: ModalProps) {
  const dialog = useRef<HTMLDivElement>(null);
  const titleId = useId();

  // Focus goes in (the dialog itself while it has nothing else) and comes back; Tab stays inside.
  useDialogFocus(dialog);

  // When the focused control inside is removed (a card that is answered), focus falls to the
  // page body and the dialog's own key handler no longer hears Escape: listen there too.
  // One listener for the dialog's life: another window listener's state update re-renders before
  // this one runs (a real key press checks microtasks between listeners), and a listener that is
  // replaced during that render is skipped for the event.
  const latest = useRef({ busy, onClose });
  latest.current = { busy, onClose };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || latest.current.busy) return;
      if (document.activeElement && document.activeElement !== document.body) return;
      // A dialog opened above this one (a confirm) is the one Escape is for.
      if (!isTopmostModal(dialog.current, document.querySelectorAll(MODAL_SELECTOR))) return;
      event.preventDefault();
      latest.current.onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          event.preventDefault();
          if (!busy) onClose();
        }
      }}
    >
      <div
        className={`dialog${wide ? " dialog-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        ref={dialog}
      >
        <DialogHead titleId={titleId} title={title} onClose={onClose} busy={busy} />
        {children}
      </div>
    </div>
  );
}

/** The title and a close button a finger can reach: on a phone or an iPad a wide dialog fills the
 * screen, so there is no backdrop to tap and maybe no Escape key. */
export function DialogHead({ titleId, title, onClose, busy = false }: { titleId: string; title: string; onClose: () => void; busy?: boolean }) {
  return (
    <div className="dialog-head">
      <h2 id={titleId}>{title}</h2>
      <button type="button" className="icon-btn dialog-close" aria-label="Close" title="Close" disabled={busy} onClick={onClose}>
        <Icon name="x" size={18} />
      </button>
    </div>
  );
}
