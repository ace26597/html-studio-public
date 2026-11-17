// The file viewer's unsaved edits: one question for every way they could be lost. Pure, so testable.

export const DISCARD_QUESTION = "Discard your unsaved changes to this file?";

/** True when it is fine to throw the edits away: there are none, or the person said so. */
export function mayDiscard(dirty: boolean, ask: (question: string) => boolean): boolean {
  return !dirty || ask(DISCARD_QUESTION);
}

/**
 * Numbers requests so a slow answer cannot land after a newer one: open file A, then B while A
 * is slow, and A's contents must not appear under B's name.
 */
export function latestOnly(): { begin: () => number; isLatest: (ticket: number) => boolean } {
  let latest = 0;
  return {
    begin: () => ++latest,
    isLatest: (ticket) => ticket === latest,
  };
}
