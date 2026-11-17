// Focus in a modal dialog: it goes in when the dialog opens and comes back when it closes, and Tab
// and Shift+Tab stay inside wherever focus is (on the page body too). The rules are pure, so testable.

import { useEffect, type RefObject } from "react";
import { isTopmostModal, MODAL_SELECTOR } from "./escape";

export const FOCUSABLE =
  'button:not(:disabled), [href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]';

interface Root<T> {
  querySelector: (selector: string) => T | null;
}

/**
 * Where focus goes when a dialog opens: the control marked `data-autofocus`, else its first
 * control that is not the close button, else the dialog itself (it has nothing else yet).
 */
export function focusTarget<T, R extends Root<T>>(dialog: R): T | R {
  return dialog.querySelector("[data-autofocus]") ?? dialog.querySelector(`:is(${FOCUSABLE}):not(.dialog-close)`) ?? dialog;
}

/** The control Tab (or Shift+Tab) moves to; `index` -1 is focus on the dialog itself or outside it. */
export function nextFocusIndex(count: number, index: number, backwards: boolean): number {
  if (index < 0) return backwards ? count - 1 : 0;
  return (index + (backwards ? count - 1 : 1)) % count;
}

/** Only the topmost dialog keeps Tab: a confirm above it keeps its own. */
export function tabKeepsInside<T>(key: string, dialog: T | null, modals: ArrayLike<T>): boolean {
  return key === "Tab" && isTopmostModal(dialog, modals);
}

/**
 * For a dialog element (give it `tabIndex={-1}`): focus in on open and back on close; Tab and
 * Shift+Tab stay inside. A dialog that opens with nothing to focus (its content still loading)
 * takes focus itself, then hands it to its first control once one appears, unless the person
 * has moved it meanwhile.
 */
export function useDialogFocus(dialog: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement as HTMLElement | null;
    let observer: MutationObserver | null = null;
    if (element) {
      const target = focusTarget<HTMLElement, HTMLElement>(element);
      target.focus();
      if (target === element) {
        observer = new MutationObserver(() => {
          const active = document.activeElement;
          if (active !== element && active !== document.body) {
            observer?.disconnect();
            return;
          }
          const first = focusTarget<HTMLElement, HTMLElement>(element);
          if (first !== element) {
            first.focus();
            observer?.disconnect();
          }
        });
        observer.observe(element, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
      }
    }

    const onKey = (event: KeyboardEvent) => {
      const current = dialog.current;
      if (!current || event.defaultPrevented) return;
      if (!tabKeepsInside(event.key, current, document.querySelectorAll(MODAL_SELECTOR))) return;
      event.preventDefault();
      const items = Array.from(current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) {
        current.focus();
        return;
      }
      const index = items.indexOf(document.activeElement as HTMLElement);
      items[nextFocusIndex(items.length, index, event.shiftKey)]?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      observer?.disconnect();
      window.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [dialog]);
}
