"use client";

/**
 * The product component set. Every screen composes these rather than
 * restyling its own markup, so typography, spacing, focus treatment and
 * status colors stay identical everywhere.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
  type Ref,
} from "react";

/* ----------------------------------------------------------------------- */
/* Buttons                                                                  */
/* ----------------------------------------------------------------------- */

type ButtonVariant = "primary" | "secondary" | "tertiary" | "destructive" | "destructive-solid";

const BUTTON_CLASS: Record<ButtonVariant, string> = {
  primary: "btn-primary",
  secondary: "btn-secondary",
  tertiary: "btn-tertiary",
  destructive: "btn-destructive",
  "destructive-solid": "btn-destructive-solid",
};

export function Button({
  variant = "secondary",
  size,
  className,
  type = "button",
  ref,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: "sm";
  ref?: Ref<HTMLButtonElement>;
}) {
  const classes = [BUTTON_CLASS[variant], size === "sm" ? "btn-sm" : "", className ?? ""]
    .filter(Boolean)
    .join(" ");
  return <button ref={ref} type={type} className={classes} {...rest} />;
}

/* ----------------------------------------------------------------------- */
/* Badges                                                                   */
/* ----------------------------------------------------------------------- */

export type BadgeTone = "gray" | "blue" | "green" | "amber" | "red" | "teal";

export function Badge({ tone = "gray", children }: { tone?: BadgeTone; children: ReactNode }) {
  const toneClass = tone === "gray" ? "" : ` badge-${tone}`;
  return <span className={`badge${toneClass}`}>{children}</span>;
}

/* ----------------------------------------------------------------------- */
/* Page structure                                                           */
/* ----------------------------------------------------------------------- */

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header-text">
        <h1>{title}</h1>
        {description !== undefined && <p>{description}</p>}
      </div>
      {actions !== undefined && <div className="button-row">{actions}</div>}
    </header>
  );
}

export function Card({
  title,
  description,
  actions,
  children,
  flush = false,
  className,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** Skip body padding — for tables and lists that manage their own. */
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`card${className ? ` ${className}` : ""}`}>
      {(title !== undefined || actions !== undefined) && (
        <div className="card-header">
          <div>
            {title !== undefined && <h2>{title}</h2>}
            {description !== undefined && <p className="card-description">{description}</p>}
          </div>
          {actions !== undefined && <div className="button-row">{actions}</div>}
        </div>
      )}
      {flush ? children : <div className="card-body">{children}</div>}
    </section>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon}
      <h3>{title}</h3>
      {description !== undefined && <p>{description}</p>}
      {action}
    </div>
  );
}

/* ----------------------------------------------------------------------- */
/* Feedback                                                                 */
/* ----------------------------------------------------------------------- */

export function InlineError({ children }: { children: ReactNode }) {
  return (
    <p className="form-error" role="alert">
      {children}
    </p>
  );
}

export function InlineSuccess({ children }: { children: ReactNode }) {
  return (
    <p className="form-success" role="status">
      {children}
    </p>
  );
}

/* ----------------------------------------------------------------------- */
/* Confirmation dialog                                                      */
/* ----------------------------------------------------------------------- */

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return;
    // Focus moves into the dialog and returns to the opener afterwards.
    openerRef.current = document.activeElement;
    cancelRef.current?.focus();
    return () => {
      if (openerRef.current instanceof HTMLElement) openerRef.current.focus();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="dialog-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
      >
        <h2 id={titleId}>{title}</h2>
        <p>{description}</p>
        <div className="dialog-actions">
          <Button ref={cancelRef} onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "destructive-solid" : "primary"}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------- */
/* Modal form dialog                                                        */
/* ----------------------------------------------------------------------- */

export function FormDialog({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement;
    // Focus the first focusable control inside the dialog.
    const first = dialogRef.current?.querySelector<HTMLElement>(
      "input, select, textarea, button",
    );
    first?.focus();
    return () => {
      if (openerRef.current instanceof HTMLElement) openerRef.current.focus();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="dialog-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------- */
/* Toasts                                                                   */
/* ----------------------------------------------------------------------- */

type Toast = { id: number; message: string; tone: "default" | "error" };

const ToastContext = createContext<(message: string, tone?: "default" | "error") => void>(
  () => {},
);

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const push = useCallback((message: string, tone: "default" | "error" = "default") => {
    const id = nextId.current;
    nextId.current += 1;
    setToasts((current) => [...current, { id, message, tone }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 5000);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-region">
        {toasts.map((toast) => (
          <p
            key={toast.id}
            className={toast.tone === "error" ? "toast toast-error" : "toast"}
            role={toast.tone === "error" ? "alert" : "status"}
          >
            {toast.message}
          </p>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/* ----------------------------------------------------------------------- */
/* Tabs / section navigation                                                */
/* ----------------------------------------------------------------------- */

export function SectionNav<T extends string>({
  sections,
  active,
  onSelect,
  label,
}: {
  sections: ReadonlyArray<{ key: T; label: string }>;
  active: T;
  onSelect: (key: T) => void;
  label: string;
}) {
  return (
    <div className="section-nav" role="tablist" aria-label={label}>
      {sections.map((section) => (
        <button
          key={section.key}
          type="button"
          role="tab"
          aria-selected={section.key === active}
          onClick={() => onSelect(section.key)}
        >
          {section.label}
        </button>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------------------- */
/* Actions menu                                                             */
/* ----------------------------------------------------------------------- */

export function ActionsMenu({
  label,
  children,
}: {
  label: string;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!anchorRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="menu-anchor" ref={anchorRef}>
      <Button size="sm" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        {label}
      </Button>
      {open && <div className="menu-popover" role="menu">{children(() => setOpen(false))}</div>}
    </div>
  );
}
