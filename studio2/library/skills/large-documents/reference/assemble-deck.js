// The one command that builds the deck from the plan (copy to tools/assemble_deck.js):
//
//   NODE_PATH=tools/node_modules node tools/assemble_deck.js                 # the whole deck
//   NODE_PATH=tools/node_modules node tools/assemble_deck.js --only market   # one section, to /tmp
//
// It reads build/outline.json, runs each section's module in the outline's order, checks that
// each module made exactly the slides the outline gives it (by id, in order), writes the file
// and the theme, and writes build/slide-map.json (slide number → section, id, module, layout) so a
// defect found at "Slide 41" names the module to redo. Nothing here sets a colour or a position.
const fs = require("fs");
const path = require("path");
const { makeDeck } = require("./deck_helper.js");

const ROOT = path.resolve(__dirname, "..");

async function main() {
  const outline = JSON.parse(fs.readFileSync(path.join(ROOT, "build/outline.json"), "utf8"));
  const at = process.argv.indexOf("--only");
  const only = at > 0 ? process.argv[at + 1] : null;
  const name = outline.file || "deck";
  const out = only ? `/tmp/${name}/section-${only}.pptx` : `files/${name}.pptx`;
  if (only && !outline.sections.some((s) => s.id === only)) throw new Error(`No section "${only}" in build/outline.json.`);
  fs.mkdirSync(path.dirname(path.resolve(ROOT, out)), { recursive: true });

  const deck = makeDeck();
  const problems = [];
  for (const section of outline.sections) {
    if (only && section.id !== only) continue;
    const before = deck.made.length;
    deck.begin(section);
    const file = path.join(ROOT, section.module);
    if (!fs.existsSync(file)) { problems.push(`${section.id}: ${section.module} does not exist yet`); continue; }
    delete require.cache[require.resolve(file)];
    require(file)(deck.h, section);
    const wanted = section.slides.map((s) => s.id);
    const got = deck.made.slice(before).map((s) => s.id);
    if (wanted.join("|") !== got.join("|"))
      problems.push(`${section.id}: the outline has [${wanted.join(", ")}], the module made [${got.join(", ")}]`);
  }
  const made = await deck.write(out);
  if (!only) {
    const modules = Object.fromEntries(outline.sections.map((s) => [s.id, s.module]));
    const map = made.map((s, i) => ({ slide: i + 1, section: s.section, id: s.id, module: modules[s.section], layout: s.layout }));
    fs.writeFileSync(path.join(ROOT, "build/slide-map.json"), JSON.stringify(map, null, 1));
  }
  console.log(`${out}: ${made.length} slides`);
  if (problems.length) {
    console.error("The deck does not match the outline:\n- " + problems.join("\n- "));
    process.exit(1);
  }
}

main().catch((error) => { console.error(error.message); process.exit(1); });
