// Shared primitives: toasts, a popover menu, a confirm dialog and an icon button.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { Icon, type IconName } from "./icons";

// ---- Toasts ----------------------------------------------------------------

export interface ToastInput {
  message: string;
  kind?: "info" | "success" | "error";
  action?: { label: string; onClick: () => void };
  /** Milliseconds before it leaves; errors and toasts with an action stay longer. */
  duration?: number;
}

interface ToastItem extends ToastInput {
  id: number;
}

const ToastContext = createContext<(toast: ToastInput) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setToasts((list) => list.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (input: ToastInput) => {
      const id = nextId.current++;
      setToasts((list) => [...list.slice(-2), { ...input, id }]);
      const duration = input.duration ?? (input.action ? 9000 : input.kind === "error" ? 6500 : 3200);
      timers.current.set(
        id,
        window.setTimeout(() => dismiss(id), duration),
      );
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((timer) => window.clearTimeout(timer));
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`toast toast-${toast.kind ?? "info"}`}
            role={toast.kind === "error" ? "alert" : "status"}
          >
            {toast.kind === "success" && <Icon name="check" size={15} className="toast-icon" />}
            {toast.kind === "error" && <Icon name="alert" size={15} className="toast-icon" />}
            <span className="toast-message">{toast.message}</span>
            {toast.action && (
              <button
                type="button"
                className="toast-action"
                onClick={() => {
                  toast.action?.onClick();
                  dismiss(toast.id);
                }}
              >
                {toast.action.label}
              </button>
            )}
            <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => dismiss(toast.id)}>
              <Icon name="x" size={13} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ---- Icon button -------------------------------------------------------------

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName;
  label: string;
  /** Tooltip text; defaults to the label. */
  tip?: string;
  tipSide?: "top" | "bottom";
  tipAlign?: "center" | "start" | "end";
  active?: boolean;
  size?: number;
}

export function IconButton({
  icon,
  label,
  tip,
  tipSide = "bottom",
  tipAlign = "center",
  active,
  size = 16,
  className,
  ...rest
}: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      data-tip={tip ?? label}
      data-tip-side={tipSide}
      data-tip-align={tipAlign}
      aria-pressed={active === undefined ? undefined : active}
      className={`icon-btn${active ? " is-active" : ""}${className ? ` ${className}` : ""}`}
      {...rest}
    >
      <Icon name={icon} size={size} />
    </button>
  );
}

// ---- Menu --------------------------------------------------------------------

export type MenuEntry =
  | {
      type?: "item";
      label: string;
      hint?: string;
      icon?: IconName;
      danger?: boolean;
      disabled?: boolean;
      checked?: boolean;
      /** With `checked`: a switch of its own (a tick box), not one choice among its neighbours. */
      toggle?: boolean;
      onSelect: () => void;
    }
  | { type: "section"; label: string; note?: string | null }
  | { type: "separator" };

interface MenuProps {
  entries: MenuEntry[];
  /** Renders the trigger; spread `props` on a button. */
  trigger: (props: {
    onClick: () => void;
    "aria-haspopup": "menu";
    "aria-expanded": boolean;
    "aria-controls": string;
  }) => ReactNode;
  align?: "start" | "end";
  side?: "bottom" | "top";
  label: string;
  onOpenChange?: (open: boolean) => void;
}

export function Menu({ entries, trigger, align = "end", side = "bottom", label, onOpenChange }: MenuProps) {
  const [open, setOpenState] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();

  const setOpen = useCallback(
    (value: boolean) => {
      setOpenState(value);
      onOpenChange?.(value);
    },
    [onOpenChange],
  );

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    };
    // The preview iframe swallows clicks; losing window focus to it closes the menu too.
    const onBlur = () => setOpen(false);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("blur", onBlur);
    const first = list.current?.querySelector<HTMLElement>('[role^="menuitem"]:not(:disabled)');
    first?.focus();
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("blur", onBlur);
    };
  }, [open, setOpen]);

  // A menu opened near the edge of a narrow screen (the model menu on a phone) is moved back
  // inside it. Margins, not a transform: the opening animation owns the transform.
  useLayoutEffect(() => {
    const menu = list.current;
    if (!open || !menu) return;
    menu.style.marginLeft = menu.style.marginRight = "";
    const rect = menu.getBoundingClientRect();
    const edge = 8;
    const over = rect.right - (window.innerWidth - edge);
    if (over > 0) {
      const shift = Math.min(over, Math.max(0, rect.left - edge));
      if (align === "start") menu.style.marginLeft = `${-shift}px`;
    } else if (rect.left < edge && align === "end") {
      menu.style.marginRight = `${rect.left - edge}px`;
    }
  }, [open, align, entries]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open) return;
    if (event.key === "Escape") {
      event.stopPropagation();
      setOpen(false);
      wrap.current?.querySelector<HTMLElement>("button")?.focus();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const items = Array.from(list.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not(:disabled)') ?? []);
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLElement);
    let next = 0;
    if (event.key === "ArrowDown") next = (current + 1) % items.length;
    else if (event.key === "ArrowUp") next = (current - 1 + items.length) % items.length;
    else if (event.key === "End") next = items.length - 1;
    items[next].focus();
  };

  return (
    <span className="menu-wrap" ref={wrap} onKeyDown={onKeyDown}>
      {trigger({
        onClick: () => setOpen(!open),
        "aria-haspopup": "menu",
        "aria-expanded": open,
        "aria-controls": id,
      })}
      {open && (
        <div id={id} ref={list} className={`menu menu-${align} menu-${side}`} role="menu" aria-label={label}>
          {entries.map((entry, index) => {
            if (entry.type === "separator") return <div key={index} className="menu-separator" role="separator" />;
            if (entry.type === "section") {
              return (
                <div key={index} className="menu-section">
                  <span>{entry.label}</span>
                  {entry.note && <span className="menu-section-note">{entry.note}</span>}
                </div>
              );
            }
            return (
              <button
                key={index}
                type="button"
                role={entry.checked === undefined ? "menuitem" : entry.toggle ? "menuitemcheckbox" : "menuitemradio"}
                aria-checked={entry.checked === undefined ? undefined : entry.checked}
                className={`menu-item${entry.danger ? " is-danger" : ""}`}
                disabled={entry.disabled}
                onClick={() => {
                  setOpen(false);
                  entry.onSelect();
                }}
              >
                {entry.icon && <Icon name={entry.icon} size={15} />}
                <span className="menu-item-label">{entry.label}</span>
                {entry.hint && <span className="menu-item-hint">{entry.hint}</span>}
                {/* A row that can be ticked keeps the tick's place when it is not, so nothing shifts or is squeezed. */}
                {entry.checked !== undefined && (
                  <Icon name="check" size={14} className={entry.checked ? "menu-item-check" : "menu-item-check is-off"} />
                )}
              </button>
            );
          })}
        </div>
      )}
    </span>
  );
}

// ---- Confirm dialog ------------------------------------------------------------

export interface ConfirmOptions {
  title: string;
  body?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
}

interface ConfirmState extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

const ConfirmContext = createContext<(options: ConfirmOptions) => Promise<boolean>>(async () => false);

export function useConfirm() {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConfirmState | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const titleId = useId();

  const confirm = useCallback((options: ConfirmOptions) => {
    restoreFocus.current = document.activeElement as HTMLElement | null;
    return new Promise<boolean>((resolve) => setState({ ...options, resolve }));
  }, []);

  const close = useCallback(
    (ok: boolean) => {
      state?.resolve(ok);
      setState(null);
      restoreFocus.current?.focus?.();
    },
    [state],
  );

  useEffect(() => {
    if (state) cancelRef.current?.focus();
  }, [state]);

  const value = useMemo(() => confirm, [confirm]);

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {state && (
        <div
          className="dialog-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) close(false);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              close(false);
            }
            if (event.key === "Tab") {
              // Two buttons: keep focus inside the dialog.
              const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button"));
              const index = buttons.indexOf(document.activeElement as HTMLElement);
              event.preventDefault();
              buttons[(index + (event.shiftKey ? buttons.length - 1 : 1)) % buttons.length]?.focus();
            }
          }}
        >
          <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby={titleId}>
            <h2 id={titleId}>{state.title}</h2>
            {state.body && <div className="dialog-body">{state.body}</div>}
            <div className="dialog-actions">
              <button type="button" ref={cancelRef} className="btn" onClick={() => close(false)}>
                Cancel
              </button>
              <button
                type="button"
                className={`btn ${state.danger ? "btn-danger" : "btn-primary"}`}
                onClick={() => close(true)}
              >
                {state.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}
