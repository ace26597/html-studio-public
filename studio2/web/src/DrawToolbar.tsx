// Draw mode's toolbar: lives in the shell, over the page. The marks themselves
// are drawn inside the frame by the bridge.

import { DRAW_COLORS, DRAW_TOOLS, marksLabel, toolLine } from "./drawing";
import { Icon } from "./icons";
import type { DrawTool } from "./types";

interface DrawToolbarProps {
  tool: DrawTool;
  color: string;
  count: number;
  /** Why the page shown is not the newest (a reload waits for the drawing), or null. */
  note?: string | null;
  onTool: (tool: DrawTool) => void;
  onColor: (color: string) => void;
  onUndo: () => void;
  onClear: () => void;
  onDone: () => void;
}

function arrowKeys<T>(items: readonly T[], current: T, pick: (value: T) => void) {
  return (event: React.KeyboardEvent<HTMLElement>) => {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const index = items.indexOf(current);
    const next = items[(index + step + items.length) % items.length];
    pick(next);
    const group = event.currentTarget.closest('[role="radiogroup"]');
    window.setTimeout(() => group?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus(), 0);
  };
}

export function DrawToolbar({ tool, color, count, note, onTool, onColor, onUndo, onClear, onDone }: DrawToolbarProps) {
  const tools = DRAW_TOOLS.map((item) => item.id);
  const colors = DRAW_COLORS.map((item) => item.value);
  return (
    <div className="draw-bar" role="toolbar" aria-label="Drawing tools">
      <div className="draw-row">
        <div className="draw-tools" role="radiogroup" aria-label="Tool">
          {DRAW_TOOLS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={tool === item.id}
              tabIndex={tool === item.id ? 0 : -1}
              className={`draw-tool${tool === item.id ? " is-on" : ""}`}
              onClick={() => onTool(item.id)}
              onKeyDown={arrowKeys(tools, tool, onTool)}
            >
              <Icon name={item.icon} size={16} />
              <span className="draw-tool-label">{item.label}</span>
            </button>
          ))}
        </div>
        <div className="draw-colors" role="radiogroup" aria-label="Colour">
          {DRAW_COLORS.map((item) => (
            <button
              key={item.value}
              type="button"
              role="radio"
              aria-checked={color === item.value}
              aria-label={item.label}
              tabIndex={color === item.value ? 0 : -1}
              className={`draw-color${color === item.value ? " is-on" : ""}`}
              style={{ "--swatch": item.value } as React.CSSProperties}
              onClick={() => onColor(item.value)}
              onKeyDown={arrowKeys(colors, color, onColor)}
            />
          ))}
        </div>
        <span className="draw-count" aria-live="polite">
          {count ? marksLabel(count) : "No marks"}
        </span>
        <span className="draw-spacer" />
        <button type="button" className="btn btn-small" disabled={!count} onClick={onUndo}>
          Undo
        </button>
        <button type="button" className="btn btn-small" disabled={!count} onClick={onClear}>
          Clear
        </button>
        <button type="button" className="btn btn-small btn-primary" onClick={onDone}>
          Done
        </button>
      </div>
      <p className="draw-hint">{toolLine(tool)}</p>
      {note && (
        <p className="draw-hint draw-note" role="status">
          {note}
        </p>
      )}
    </div>
  );
}
