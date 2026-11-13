// The composer's compact depth row: what it says while folded.
import { DEPTHS, type TurnOptions } from "./effort";

/** "Deep · Check on": the depth and whether the result is checked, readable without opening the row. */
export function effortSummary(options: TurnOptions): string {
  const depth = (DEPTHS.find((d) => d.id === options.depth) ?? DEPTHS[1]).label;
  return `${depth} · Check ${options.verify ? "on" : "off"}`;
}
