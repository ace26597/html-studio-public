// A modal dialog: focus goes in and comes back, Tab stays inside, Escape and a backdrop click close it.

import { useEffect, useId, useRef, type ReactNode } from "react";
import { isTopmostModal, MODAL_SELECTOR } from "./escape";

interface ModalProps {
  title: string;
  onClose: () => void;
  /** While true, Escape and the backdrop do nothing (a request is in flight). */
  busy?: boolean;
  wide?: boolean;
  children: ReactNode;
}

const FOCUSABLE =
  'button:not(:disabled), [href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]';

export function Modal({ title, onClose, busy = false, wide = false, children }: ModalProps) {
  const dialog = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const first = dialog.current?.querySelector<HTMLElement>("[data-autofocus]") ?? dialog.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    return () => previous?.focus?.();
  }, []);

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
        } else if (event.key === "Tab") {
          const items = Array.from(dialog.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
          if (!items.length) return;
          const index = items.indexOf(document.activeElement as HTMLElement);
          event.preventDefault();
          items[(index + (event.shiftKey ? items.length - 1 : 1) + items.length) % items.length]?.focus();
        }
      }}
    >
      <div
        className={`dialog${wide ? " dialog-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={dialog}
      >
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}
