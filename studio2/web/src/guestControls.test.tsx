// A guest on a shared studio is not offered what only its owner may do (CONTRACT.md, "What a guest
// may not do on a shared studio").
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { approvalsEntries } from "./Chat";
import { NewProjectDialog } from "./NewProjectDialog";
import { initialThread, reduceEvent, waitingApprovals, type ApprovalItem } from "./thread";
import { ApprovalCard } from "./ThreadView";
import type { StudioEvent } from "./types";
import type { MenuEntry } from "./ui";

const noop = () => undefined;

describe("New page for a guest", () => {
  it("offers no app on this computer when there is no way to bring one in", () => {
    const guest = renderToStaticMarkup(<NewProjectDialog creating={false} createError={null} onCreate={noop} onClose={noop} />);
    expect(guest).not.toContain("An app on this computer");
    const owner = renderToStaticMarkup(
      <NewProjectDialog creating={false} createError={null} onCreate={noop} onAttach={noop} onClose={noop} />,
    );
    expect(owner).toContain("An app on this computer");
  });
});

// ---- approval cards and "Allow everything" ------------------------------------------------------
const card = (ownerOnly: boolean | undefined): ApprovalItem => ({
  type: "approval",
  key: "approval:a_1",
  approvalId: "a_1",
  turnId: "t_1",
  tool: "Bash",
  title: "Run a shell command",
  detail: "rm -rf build",
  reason: "Deletes files",
  ...(ownerOnly === undefined ? {} : { ownerOnly }),
  status: "waiting",
  by: null,
  seq: 1,
});
const cardActions = (guest: boolean | undefined, error: string | null = null) =>
  ({
    decisions: error ? { a_1: { pending: null, error } } : {},
    onDecide: noop,
    ...(guest === undefined ? {} : { guest }),
  }) as unknown as Parameters<typeof ApprovalCard>[0]["actions"];
const drawn = (ownerOnly: boolean | undefined, guest: boolean | undefined, error: string | null = null) =>
  renderToStaticMarkup(<ApprovalCard item={card(ownerOnly)} actions={cardActions(guest, error)} />);

describe("An approval card only the owner may allow", () => {
  it("offers a guest Deny and a plain line, no Allow", () => {
    const html = drawn(true, true);
    expect(html).not.toContain("Allow: Run a shell command");
    expect(html).toContain("Deny: Run a shell command");
    expect(html).toContain("Only the studio’s owner can allow this.");
    expect(html).toContain("approval-owner-note");
    expect(html).not.toContain("The agent is waiting for you.");
  });

  it("is unchanged for the owner, on a studio that is not shared, and for a guest's own card", () => {
    const before = drawn(undefined, undefined);
    expect(before).toContain("Allow: Run a shell command");
    expect(before).toContain("The agent is waiting for you.");
    expect(drawn(true, false)).toBe(before); // the owner of a shared studio
    expect(drawn(false, false)).toBe(before); // a studio that is not shared (never marked)
    expect(drawn(undefined, false)).toBe(before);
    expect(drawn(false, true)).toBe(before); // a guest's own ordinary card
    expect(drawn(undefined, true)).toBe(before);
  });

  it("still shows a refusal that arrives anyway", () => {
    const html = drawn(false, true, "Only this studio's owner can allow this.");
    expect(html).toContain('role="alert"');
    expect(html).toContain("Only this studio&#x27;s owner can allow this.");
  });

  it("is marked from the server's field, and from guarded on an older event", () => {
    const one = (extra: Record<string, unknown>) => {
      const event = {
        type: "approval_request",
        seq: 1,
        ts: "2026-01-01T00:00:00Z",
        turn_id: "t_1",
        approval_id: "a_1",
        tool: "Bash",
        title: "Run a shell command",
        detail: "ls",
        reason: "r",
        ...extra,
      } as unknown as StudioEvent;
      return waitingApprovals(reduceEvent(initialThread, event))[0]?.ownerOnly;
    };
    expect(one({ owner_only: true })).toBe(true);
    expect(one({ guarded: true })).toBe(true);
    expect(one({})).toBe(false);
  });
});

describe("Allow everything in the approvals menu", () => {
  type Row = Extract<MenuEntry, { onSelect: () => void }>;
  const rows = (value: "ask" | "allow", guest?: boolean) => {
    const chosen: string[] = [];
    const entries = approvalsEntries(value, (v) => chosen.push(v), guest);
    const items = entries.filter((e): e is Row => "onSelect" in e);
    return { entries, chosen, ask: items[0], allow: items[1] };
  };

  it("cannot be chosen by a guest, and shows when the owner set it", () => {
    const off = rows("ask", true);
    expect(off.allow).toMatchObject({ label: "Allow everything", disabled: true, checked: false, hint: "Owner only" });
    off.allow.onSelect();
    expect(off.chosen).toEqual([]);
    const on = rows("allow", true);
    expect(on.allow).toMatchObject({ disabled: true, checked: true });
    expect(on.ask).toMatchObject({ checked: false });
    expect(on.ask.disabled).toBeFalsy();
    on.ask.onSelect(); // a guest may set it back to asking
    expect(on.chosen).toEqual(["ask"]);
  });

  it("is unchanged for the owner and on a studio that is not shared", () => {
    for (const value of ["ask", "allow"] as const) {
      const plain = rows(value);
      expect(JSON.stringify(rows(value, false).entries)).toBe(JSON.stringify(plain.entries));
      expect(plain.allow.disabled).toBeUndefined();
      expect(plain.allow.hint).toBeUndefined();
      plain.allow.onSelect();
      expect(plain.chosen).toEqual(["allow"]);
    }
  });
});
