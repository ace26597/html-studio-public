import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PlacesList } from "./LibraryPlacesBlock";
import { placeNote, withPath, type LibraryPlacesInfo } from "./libraryPlaces";

const info: LibraryPlacesInfo = {
  always: ["~/.claude/skills", "the plugins installed for Claude Code", "~/.claude.json"],
  skill_dirs: [{ path: "/srv/skills", found: true, skills: 12 }, { path: "/gone", found: false, skills: 0 }],
  connector_files: [{ path: "/srv/mcp.json", found: true, connectors: 1 }],
  from_env: { skill_dirs: false, connector_files: true },
};

describe("more places to look", () => {
  it("adds a path once and removes it", () => {
    expect(withPath(["/a"], " /b ", true)).toEqual(["/a", "/b"]);
    expect(withPath(["/a"], "/a", true)).toEqual(["/a"]);
    expect(withPath(["/a", "/b"], "/a", false)).toEqual(["/b"]);
    expect(withPath(["/a"], "  ", true)).toEqual(["/a"]);
    expect(placeNote(true, 1, "skill", "skills", "no such folder")).toBe("1 skill found");
    expect(placeNote(false, 0, "skill", "skills", "no such folder")).toBe("no such folder");
  });

  it("lists each place with what it holds, and says when a variable wins", () => {
    const html = renderToStaticMarkup(<PlacesList info={info} busy={false} onChange={() => {}} />);
    expect(html).toContain("Always read: ~/.claude/skills");
    expect(html).toContain("12 skills found");
    expect(html).toContain("no such folder");
    expect(html).toContain("1 connector found");
    expect(html.match(/Remove/g)?.length).toBe(3);
    expect(html.match(/environment variable/g)?.length).toBe(1);
  });
});
