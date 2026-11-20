import { describe, expect, it } from "vitest";
import chat from "./Chat.tsx?raw";
import questionCard from "./QuestionCard.tsx?raw";

/**
 * The tests here draw a component once (`renderToStaticMarkup`), so a hook placed after an early
 * return passes every one of them and then throws in the browser the first time the component
 * takes the other path ("Rendered more hooks than during the previous render"). That happened in
 * the chat panel on 20 November 2026: opening or collapsing it on a desktop blanked the page.
 * This reads the source instead: in these components, no hook call comes after the named return.
 */
const GUARDED: { file: string; source: string; after: string }[] = [
  { file: "Chat.tsx", source: chat, after: "if (!open && !compact) {" },
  { file: "QuestionCard.tsx", source: questionCard, after: 'if (item.status !== "waiting") return' },
];
const HOOK = /\buse(?:State|Effect|LayoutEffect|Memo|Callback|Ref|Id|Reducer|Context|ImperativeHandle|Toast)\s*[(<]/;

describe("hooks stay above a component's early return", () => {
  for (const { file, source, after } of GUARDED) {
    it(file, () => {
      const at = source.indexOf(after);
      expect(at, `the early return in ${file} moved: update this test`).toBeGreaterThan(-1);
      // Up to the end of that component: the next top-level declaration.
      const rest = source.slice(at);
      const end = rest.search(/\n(?:export )?(?:function|const) [A-Z]/);
      const body = end === -1 ? rest : rest.slice(0, end);
      const found = body.split("\n").filter((line: string) => HOOK.test(line) && !line.trim().startsWith("//"));
      expect(found, `hooks after the early return in ${file}`).toEqual([]);
    });
  }

  it("would have caught the hook that blanked the page", () => {
    const broken = "function Panel() {\n  if (!open && !compact) {\n    return null;\n  }\n  useLayoutEffect(() => {}, []);\n}\n";
    const rest = broken.slice(broken.indexOf("if (!open && !compact) {"));
    expect(rest.split("\n").some((line) => HOOK.test(line))).toBe(true);
  });
});
