// The settings strip under the composer: what its controls say.
import { DEPTHS, type TurnOptions } from "./effort";

/** The depth's name, as the button shows it ("Deep"). An unknown depth reads as Standard. */
export function effortLabel(options: TurnOptions): string {
  return (DEPTHS.find((d) => d.id === options.depth) ?? DEPTHS[1]).label;
}

/** What the Verify switch says when the pointer rests on it. */
export const VERIFY_OFF_TIP = "Off. Switch on to have every figure checked against its saved source after each request.";
export const VERIFY_ON_TIP = "On: after each request an independent check compares every figure with its saved source.";
