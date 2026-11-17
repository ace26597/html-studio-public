// The composer's depth control: what its button says.
import { DEPTHS, type TurnOptions } from "./effort";

/** The depth's name, as the button shows it ("Deep"). An unknown depth reads as Standard. */
export function effortLabel(options: TurnOptions): string {
  return (DEPTHS.find((d) => d.id === options.depth) ?? DEPTHS[1]).label;
}

/** What a screen reader hears for the button: "How much to do: Deep, and the result is checked". */
export function effortSummary(options: TurnOptions): string {
  return `How much to do: ${effortLabel(options)}${options.verify ? ", and the result is checked" : ""}`;
}
