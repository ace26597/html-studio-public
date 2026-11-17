// One Escape closes one thing: the topmost. Pure, so the order is testable.

/** Any open modal dialog (the studio's `Modal` and the confirm dialog both mark themselves so). */
export const MODAL_SELECTOR = '[aria-modal="true"]';

export interface EscapeState {
  modalOpen: boolean;
  versionSelected: boolean;
  rightPanel: boolean;
  chatOpen: boolean;
  filesOpen: boolean;
}

export type EscapeTarget = "none" | "version" | "right" | "chat" | "files";

/**
 * What the project page's window Escape closes. A dialog above the page owns Escape (it closes
 * itself), so the page closes nothing then: without this, Escape with focus on the page body
 * closed the dialog and collapsed the chat as well.
 */
export function escapeTarget(state: EscapeState): EscapeTarget {
  if (state.modalOpen) return "none";
  if (state.versionSelected) return "version";
  if (state.rightPanel) return "right";
  if (state.chatOpen) return "chat";
  if (state.filesOpen) return "files";
  return "none";
}

/** Dialogs open above each other in document order, so the topmost is the last one. */
export function isTopmostModal<T>(dialog: T | null, modals: ArrayLike<T>): boolean {
  return dialog !== null && modals.length > 0 && modals[modals.length - 1] === dialog;
}

interface EscapeKey {
  key: string;
  preventDefault: () => void;
  stopPropagation: () => void;
}

/**
 * The confirm dialog's window Escape, heard in the capture phase so it works wherever focus is
 * (on the page body, in a field behind the dialog). It cancels the dialog when it is the topmost
 * one and stops the key there, so nothing below (a Modal, the project page) also closes.
 */
export function confirmEscape<T>(event: EscapeKey, dialog: T | null, modals: ArrayLike<T>, cancel: () => void): void {
  if (event.key !== "Escape" || !isTopmostModal(dialog, modals)) return;
  event.preventDefault();
  event.stopPropagation();
  cancel();
}
