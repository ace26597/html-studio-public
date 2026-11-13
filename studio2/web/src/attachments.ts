// The composer's attachments: what goes on the next message, and what blocks sending.

export interface AttachmentState {
  id: string;
  name: string;
  status: "uploading" | "done" | "error";
  path: string | null;
}

export interface AttachmentPlan {
  /** Workspace paths to send as `attachments`, in the order they were added. */
  paths: string[];
  /** Composer ids of those attachments (cleared after a successful send). */
  ids: string[];
  /** Why sending must wait, or null when it can go. */
  blocked: "uploading" | "failed" | null;
}

/**
 * Every attachment must have uploaded before the message goes: one still
 * uploading blocks until it finishes, one that failed blocks until removed.
 * Nothing is ever dropped silently from a message.
 */
export function planAttachments(list: readonly AttachmentState[]): AttachmentPlan {
  const done = list.filter((item) => item.status === "done" && item.path);
  const blocked = list.some((item) => item.status === "error")
    ? "failed"
    : list.some((item) => item.status === "uploading")
      ? "uploading"
      : null;
  return { paths: done.map((item) => item.path as string), ids: done.map((item) => item.id), blocked };
}
