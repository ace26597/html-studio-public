import { describe, expect, it } from "vitest";
import {
  addressProblem,
  attachBlocker,
  attachDetail,
  attachLabel,
  attachRequest,
  defaultAttachName,
  envSkippedNote,
  formatBytes,
  shortPath,
  sizeLine,
} from "./attach";
import type { AttachRecord, Inspection } from "./types";

const inspection: Inspection = {
  path: "/Users/sam/dev/site",
  name: "site",
  exists: true,
  readable: true,
  writable: true,
  git: { branch: "main", head: "a".repeat(40), subject: "Second", uncommitted: [{ path: "x", status: "modified" }], uncommitted_more: 0 },
  framework: { id: "next", label: "Next.js" },
  run: "exec npx next dev -p $PORT -H 127.0.0.1",
  package_manager: "npm",
  install: "npm ci",
  env_files: [".env", ".env.local"],
  size: { files: 225, bytes: 9_100_000, history_bytes: 217_000_000, partial: false },
  refused: { copy: null, in_place: "The studio cannot both read and write this folder." },
  notes: [],
};

const record: AttachRecord = {
  mode: "copy",
  path: "/Users/sam/dev/site",
  kind: "git",
  framework: "next",
  branch: "main",
  started_from: "bbc748d0123456789",
  studio_branch: "studio/site-1a2b3c4d",
  import_commit: "c".repeat(40),
  uncommitted: [{ path: "x", status: "modified" }],
  included_uncommitted: false,
  env_files: [],
  install: "npm ci",
  address: null,
  attached_at: "2026-11-12T00:00:00Z",
};

const choices = { mode: "copy" as const, name: "", run: "npm run dev", address: "", envFiles: [] as string[], includeUncommitted: false };

describe("attach helpers", () => {
  it("shortens home paths only", () => {
    expect(shortPath("/Users/sam/dev/site")).toBe("~/dev/site");
    expect(shortPath("/home/sam")).toBe("~");
    expect(shortPath("/private/tmp/app")).toBe("/private/tmp/app");
  });

  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(9_100_000)).toBe("8.7 MB");
    expect(formatBytes(217_000_000)).toBe("207 MB");
    expect(sizeLine(inspection)).toBe("225 files, 8.7 MB, history 207 MB");
  });

  it("labels a copy and an in-place project", () => {
    expect(attachLabel(record)).toBe("Copy of ~/dev/site");
    expect(attachDetail(record)).toBe(
      "From main at bbc748d · changes go on studio/site-1a2b3c4d · 1 uncommitted change not copied · the original is not changed",
    );
    const inPlace = { ...record, mode: "in_place" as const, studio_branch: null, address: "http://localhost:5173" };
    expect(attachLabel(inPlace)).toBe("Editing ~/dev/site in place");
    expect(attachDetail(inPlace)).toContain("shows http://localhost:5173");
  });

  it("accepts only loopback addresses with a port", () => {
    for (const ok of ["", "localhost:5173", "http://127.0.0.1:3000", "http://[::1]:8080/"]) expect(addressProblem(ok)).toBeNull();
    for (const bad of ["http://192.168.1.5:3000", "https://localhost:5173", "http://localhost", "example.com:80", "::"]) {
      expect(addressProblem(bad)).not.toBeNull();
    }
  });

  it("blocks Create for a refused way in and explains why", () => {
    expect(attachBlocker(null, choices)).toBe("Check the folder first.");
    expect(attachBlocker(inspection, choices)).toBeNull();
    expect(attachBlocker(inspection, { ...choices, mode: "in_place" })).toContain("read and write");
    const writable = { ...inspection, refused: { copy: null, in_place: null } };
    expect(attachBlocker(writable, { ...choices, mode: "in_place", address: "http://10.0.0.1:80" })).toContain("this computer");
  });

  it("builds the request: env files and uncommitted work only for a copy, the address only in place", () => {
    expect(attachRequest(inspection, { ...choices, envFiles: [".env.local", ".env.made-up"], includeUncommitted: true })).toEqual({
      path: "/Users/sam/dev/site",
      mode: "copy",
      run: "npm run dev",
      env_files: [".env.local"],
      include_uncommitted: true,
    });
    expect(
      attachRequest(inspection, { ...choices, mode: "in_place", address: " localhost:5173 ", envFiles: [".env"], name: " Mine " }),
    ).toEqual({ path: "/Users/sam/dev/site", mode: "in_place", run: "npm run dev", address: "localhost:5173", name: "Mine" });
    expect(defaultAttachName(inspection, "in_place")).toBe("site (in place)");
  });
});

describe("key files a copy did not take (env_skipped)", () => {
  const link = { name: ".env", reason: "it is a link, and the studio never copies links" };
  const gone = { name: ".env.local", reason: "not found in the folder" };

  it("says nothing when every ticked file was copied, or for an older record", () => {
    expect(envSkippedNote({ ...record, env_skipped: [] })).toBeNull();
    expect(envSkippedNote(record)).toBeNull();
  });

  it("names each file, why, and what to do", () => {
    const one = envSkippedNote({ ...record, env_skipped: [link] })!;
    expect(one).toContain("The key file .env was not copied: it is a link, and the studio never copies links.");
    expect(one).toContain("replace the link in ~/dev/site with a real file");
    expect(one).toContain("make the copy again");
    const two = envSkippedNote({ ...record, env_skipped: [link, gone] })!;
    expect(two).toContain("2 key files were not copied: .env (it is a link");
    expect(two).toContain(".env.local (not found in the folder)");
    expect(two).toContain("check that it is still in ~/dev/site");
  });

  it("is kept in the project's own line about where it came from", () => {
    expect(attachDetail({ ...record, env_skipped: [link] })).toContain("1 key file not copied (.env)");
    expect(attachDetail(record)).not.toContain("key file");
  });
});
