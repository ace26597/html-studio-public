// Files are dropped on the chat (or on the Files panel), nowhere else (CONTRACT.md, "Files are
// dropped on the chat, nowhere else"). A file dropped anywhere else would make the browser show
// that file in place of the studio, so such a drop is cancelled.

interface DragLike {
  defaultPrevented: boolean;
  dataTransfer: { types: ArrayLike<string>; dropEffect?: string } | null;
  preventDefault: () => void;
}

/** True when a drag carries files (a browser may say so for a picture dragged inside a page too). */
export function carriesFiles(types: ArrayLike<string> | null | undefined): boolean {
  return !!types && Array.prototype.indexOf.call(types, "Files") !== -1;
}

/** A drag or drop of files that no drop area took: cancel it. Returns whether it was. */
export function cancelStray(event: DragLike, over: boolean): boolean {
  if (event.defaultPrevented || !carriesFiles(event.dataTransfer?.types)) return false;
  event.preventDefault();
  if (over && event.dataTransfer) event.dataTransfer.dropEffect = "none";
  return true;
}

/** Listen on the window, after every drop area has had the event. Returns how to stop. */
export function guardStrayDrops(target: Pick<Window, "addEventListener" | "removeEventListener"> = window): () => void {
  const onOver = (event: DragEvent) => void cancelStray(event, true);
  const onDrop = (event: DragEvent) => void cancelStray(event, false);
  target.addEventListener("dragover", onOver);
  target.addEventListener("drop", onDrop);
  return () => {
    target.removeEventListener("dragover", onOver);
    target.removeEventListener("drop", onDrop);
  };
}
