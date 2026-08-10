"use client";

import { Plus } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import {
  ActionsMenu,
  Badge,
  Button,
  Card,
  ConfirmDialog,
  FormDialog,
  InlineError,
  InlineSuccess,
  PageHeader,
} from "@/components/ui";
import { api, errorDetail, type Role, type SessionUser, type UserList } from "@/lib/api";
import { passwordPolicyError } from "@/lib/password";
import { ROLE_LABELS } from "@/lib/roles";

const ROLE_TONES: Record<Role, "teal" | "blue" | "gray"> = {
  owner: "teal",
  manager: "blue",
  team_member: "gray",
};

const PAGE_SIZE = 25;

export default function UsersPage() {
  const { user } = useAuth();
  if (user.role !== "owner") {
    return (
      <section>
        <PageHeader title="Users" />
        <InlineError>You do not have access to user management.</InlineError>
      </section>
    );
  }
  return <UserManagement selfId={user.id} />;
}

function UserManagement({ selfId }: { selfId: string }) {
  const { csrfToken } = useAuth();
  const [data, setData] = useState<UserList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<SessionUser | null>(null);
  const [resetting, setResetting] = useState<SessionUser | null>(null);
  const [deactivating, setDeactivating] = useState<SessionUser | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (search) params.set("query", search);
    if (roleFilter) params.set("role", roleFilter);
    if (statusFilter) params.set("status", statusFilter);
    void api<UserList>(`/users?${params.toString()}`).then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load users."));
        return;
      }
      setData(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [reloadNonce, search, roleFilter, statusFilter, page]);

  const load = useCallback(() => setReloadNonce((value) => value + 1), []);

  function feedback(message: string) {
    setError(null);
    setNotice(message);
    load();
  }

  function failure(message: string) {
    setNotice(null);
    setError(message);
  }

  async function setActive(target: SessionUser, active: boolean) {
    setBusy(true);
    const result = await api(`/users/${target.id}`, {
      method: "PATCH",
      csrfToken,
      body: { is_active: active },
    });
    setBusy(false);
    setDeactivating(null);
    if (!result.ok) failure(errorDetail(result.data, "Unable to update the user."));
    else feedback(`${target.email} ${active ? "reactivated" : "deactivated"}.`);
  }

  const totalPages = data === null ? 1 : Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const showFilters = (data?.total ?? 0) > 5 || search !== "" || roleFilter !== "" || statusFilter !== "";

  return (
    <section>
      <PageHeader
        title="Users"
        description="Who can sign in, and what they are allowed to do."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus size={16} aria-hidden="true" />
            Create user
          </Button>
        }
      />

      {error !== null && <InlineError>{error}</InlineError>}
      {notice !== null && <InlineSuccess>{notice}</InlineSuccess>}

      {showFilters && (
        <div className="toolbar">
          <div className="form-field" style={{ flex: "1 1 12rem" }}>
            <label htmlFor="user-search">Search</label>
            <input
              id="user-search"
              type="search"
              placeholder="Email or name"
              value={search}
              onChange={(event) => {
                setPage(1);
                setSearch(event.target.value);
              }}
            />
          </div>
          <div className="form-field">
            <label htmlFor="user-role-filter">Role</label>
            <select
              id="user-role-filter"
              value={roleFilter}
              onChange={(event) => {
                setPage(1);
                setRoleFilter(event.target.value);
              }}
            >
              <option value="">All</option>
              {Object.entries(ROLE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="user-status-filter">Status</label>
            <select
              id="user-status-filter"
              value={statusFilter}
              onChange={(event) => {
                setPage(1);
                setStatusFilter(event.target.value);
              }}
            >
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
        </div>
      )}

      {data === null ? (
        <p className="page-status" role="status">
          Loading users…
        </p>
      ) : data.items.length === 0 ? (
        <Card>
          <p className="card-empty">No users match these filters.</p>
        </Card>
      ) : (
        <Card flush className="table-card">
          <table className="data-table">
            <caption className="visually-hidden">Existing users</caption>
            <thead>
              <tr>
                <th scope="col">User</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((target) => {
                const isSelf = target.id === selfId;
                return (
                  <tr key={target.id}>
                    <td>
                      <span style={{ fontWeight: 600 }}>
                        {target.display_name || target.email}
                      </span>
                      {isSelf && <span className="cell-secondary"> (you)</span>}
                      {target.display_name !== "" && (
                        <div className="cell-secondary">{target.email}</div>
                      )}
                      {target.notification_phone !== null && (
                        <div className="cell-secondary">{target.notification_phone}</div>
                      )}
                    </td>
                    <td>
                      <Badge tone={ROLE_TONES[target.role]}>{ROLE_LABELS[target.role]}</Badge>
                    </td>
                    <td>
                      {target.is_active ? (
                        <Badge tone="green">Active</Badge>
                      ) : (
                        <Badge>Inactive</Badge>
                      )}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <ActionsMenu label={`Actions for ${target.email}`}>
                        {(close) => (
                          <>
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                close();
                                setEditing(target);
                              }}
                            >
                              Edit user
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                close();
                                setResetting(target);
                              }}
                            >
                              Set temporary password
                            </button>
                            {target.is_active ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="menu-destructive"
                                disabled={isSelf}
                                onClick={() => {
                                  close();
                                  setDeactivating(target);
                                }}
                              >
                                Deactivate
                              </button>
                            ) : (
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                  close();
                                  void setActive(target, true);
                                }}
                              >
                                Reactivate
                              </button>
                            )}
                            {isSelf && (
                              <span className="menu-hint">
                                You cannot deactivate your own account.
                              </span>
                            )}
                          </>
                        )}
                      </ActionsMenu>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}

      {data !== null && data.total > PAGE_SIZE && (
        <nav className="pagination" aria-label="User pages">
          <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            Previous
          </Button>
          <span>
            Page {page} of {totalPages} · {data.total} users
          </span>
          <Button size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </nav>
      )}

      <FormDialog open={creating} title="Create user" onClose={() => setCreating(false)}>
        <CreateUserForm
          csrfToken={csrfToken}
          onCreated={(email) => {
            setCreating(false);
            feedback(`User ${email} created with a temporary password.`);
          }}
        />
      </FormDialog>

      <FormDialog
        open={editing !== null}
        title="Edit user"
        onClose={() => setEditing(null)}
      >
        {editing !== null && (
          <EditUserForm
            target={editing}
            isSelf={editing.id === selfId}
            csrfToken={csrfToken}
            onDone={(message) => {
              setEditing(null);
              feedback(message);
            }}
          />
        )}
      </FormDialog>

      <FormDialog
        open={resetting !== null}
        title="Set a temporary password"
        onClose={() => setResetting(null)}
      >
        {resetting !== null && (
          <ResetPasswordForm
            target={resetting}
            csrfToken={csrfToken}
            onDone={(message) => {
              setResetting(null);
              feedback(message);
            }}
          />
        )}
      </FormDialog>

      <ConfirmDialog
        open={deactivating !== null}
        title={`Deactivate ${deactivating?.email ?? ""}?`}
        description="They are signed out everywhere and can no longer log in. Their leads, notes and history stay exactly as they are, and you can reactivate them at any time."
        confirmLabel="Deactivate"
        destructive
        busy={busy}
        onConfirm={() => {
          if (deactivating !== null) void setActive(deactivating, false);
        }}
        onCancel={() => setDeactivating(null)}
      />
    </section>
  );
}

/** Role changes and the optional presentation fields, saved deliberately
 * from one dialog — never through an unconfirmed inline control. */
function EditUserForm({
  target,
  isSelf,
  csrfToken,
  onDone,
}: {
  target: SessionUser;
  isSelf: boolean;
  csrfToken: string;
  onDone: (message: string) => void;
}) {
  const [role, setRole] = useState<Role>(target.role);
  const [displayName, setDisplayName] = useState(target.display_name);
  const [notificationPhone, setNotificationPhone] = useState(target.notification_phone ?? "");
  const [error, setError] = useState<string | null>(null);
  const [confirmingRole, setConfirmingRole] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const roleChanged = role !== target.role;

  async function save() {
    setError(null);
    setSubmitting(true);
    const body: Record<string, unknown> = {
      display_name: displayName,
      notification_phone: notificationPhone,
    };
    if (roleChanged) body.role = role;
    const result = await api(`/users/${target.id}`, { method: "PATCH", csrfToken, body });
    setSubmitting(false);
    setConfirmingRole(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to update the user."));
      return;
    }
    onDone(`${target.email} updated.`);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (roleChanged) {
      // A role is a sensitive permission change: confirm it explicitly.
      setConfirmingRole(true);
      return;
    }
    void save();
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <p className="card-description" style={{ marginBottom: "1rem" }}>
        {target.email}
      </p>
      <div className="form-field">
        <label htmlFor="edit-role">Role</label>
        <select
          id="edit-role"
          value={role}
          disabled={isSelf}
          onChange={(event) => setRole(event.target.value as Role)}
        >
          {Object.entries(ROLE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        {isSelf && <p className="form-help">You cannot change your own role.</p>}
      </div>
      <div className="form-field">
        <label htmlFor="edit-display-name">Display name (optional)</label>
        <input
          id="edit-display-name"
          maxLength={100}
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
        />
        <p className="form-help">Shown to customers instead of the email address.</p>
      </div>
      <div className="form-field">
        <label htmlFor="edit-notification-phone">Notification phone (optional)</label>
        <input
          id="edit-notification-phone"
          type="tel"
          value={notificationPhone}
          onChange={(event) => setNotificationPhone(event.target.value)}
        />
        <p className="form-help">
          International format, for example +15555550123. Used for staff alerts; never shown
          publicly.
        </p>
      </div>
      {error !== null && <InlineError>{error}</InlineError>}
      <div className="dialog-actions">
        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? "Saving…" : "Save changes"}
        </Button>
      </div>

      <ConfirmDialog
        open={confirmingRole}
        title={`Change ${target.email} to ${ROLE_LABELS[role]}?`}
        description="Roles decide what this person can see and do across the CRM. The change takes effect immediately."
        confirmLabel="Change role"
        busy={submitting}
        onConfirm={() => void save()}
        onCancel={() => setConfirmingRole(false)}
      />
    </form>
  );
}

function ResetPasswordForm({
  target,
  csrfToken,
  onDone,
}: {
  target: SessionUser;
  csrfToken: string;
  onDone: (message: string) => void;
}) {
  const [tempPassword, setTempPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const policyError = passwordPolicyError(tempPassword);
    if (policyError !== null) {
      setError(policyError);
      return;
    }
    setSubmitting(true);
    const result = await api(`/users/${target.id}/reset-password`, {
      method: "POST",
      csrfToken,
      body: { temporary_password: tempPassword },
    });
    setSubmitting(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to set a temporary password."));
      return;
    }
    onDone(`Temporary password set for ${target.email}; change required at next login.`);
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <p className="card-description" style={{ marginBottom: "1rem" }}>
        {target.email} must change it at their next sign-in.
      </p>
      <div className="form-field">
        <label htmlFor={`temp-password-${target.id}`}>
          Temporary password for {target.email}
        </label>
        <input
          id={`temp-password-${target.id}`}
          type="password"
          autoComplete="new-password"
          required
          value={tempPassword}
          onChange={(event) => setTempPassword(event.target.value)}
        />
        <p className="form-help">At least 12 characters.</p>
      </div>
      {error !== null && <InlineError>{error}</InlineError>}
      <div className="dialog-actions">
        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? "Applying…" : "Apply"}
        </Button>
      </div>
    </form>
  );
}

function CreateUserForm({
  csrfToken,
  onCreated,
}: {
  csrfToken: string;
  onCreated: (email: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("team_member");
  const [tempPassword, setTempPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const policyError = passwordPolicyError(tempPassword);
    if (policyError !== null) {
      setError(policyError);
      return;
    }
    setSubmitting(true);
    const result = await api("/users", {
      method: "POST",
      csrfToken,
      body: { email, role, temporary_password: tempPassword },
    });
    setSubmitting(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to create the user."));
      return;
    }
    const created = email;
    setEmail("");
    setRole("team_member");
    setTempPassword("");
    onCreated(created);
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div className="form-field">
        <label htmlFor="create-email">Email</label>
        <input
          id="create-email"
          type="email"
          autoComplete="off"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="create-role">Role</label>
        <select
          id="create-role"
          value={role}
          onChange={(event) => setRole(event.target.value as Role)}
        >
          {Object.entries(ROLE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="form-field">
        <label htmlFor="create-temp-password">Temporary password</label>
        <input
          id="create-temp-password"
          type="password"
          autoComplete="new-password"
          required
          aria-describedby="create-temp-password-help"
          value={tempPassword}
          onChange={(event) => setTempPassword(event.target.value)}
        />
        <p id="create-temp-password-help" className="form-help">
          At least 12 characters. The user must change it at first login.
        </p>
      </div>
      {error !== null && <InlineError>{error}</InlineError>}
      <div className="dialog-actions">
        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? "Creating…" : "Create user"}
        </Button>
      </div>
    </form>
  );
}
