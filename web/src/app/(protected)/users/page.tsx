"use client";

import { Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import {
  Badge,
  Button,
  ActionsMenu,
  Card,
  ConfirmDialog,
  FormDialog,
  InlineError,
  InlineSuccess,
  PageHeader,
} from "@/components/ui";
import { api, errorDetail, type Role, type SessionUser } from "@/lib/api";
import { passwordPolicyError } from "@/lib/password";
import { ROLE_LABELS } from "@/lib/roles";

const ROLE_TONES: Record<Role, "teal" | "blue" | "gray"> = {
  owner: "teal",
  manager: "blue",
  team_member: "gray",
};

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
  const [users, setUsers] = useState<SessionUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<SessionUser | null>(null);
  const [deactivating, setDeactivating] = useState<SessionUser | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void api<SessionUser[]>("/users").then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load users."));
        return;
      }
      setUsers(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [reloadNonce]);

  const load = useCallback(() => setReloadNonce((value) => value + 1), []);

  const visible = useMemo(() => {
    if (users === null) return null;
    return users.filter((target) => {
      if (search && !target.email.toLowerCase().includes(search.toLowerCase())) return false;
      if (roleFilter && target.role !== roleFilter) return false;
      if (statusFilter === "active" && !target.is_active) return false;
      if (statusFilter === "inactive" && target.is_active) return false;
      return true;
    });
  }, [users, search, roleFilter, statusFilter]);

  function feedback(message: string) {
    setError(null);
    setNotice(message);
    load();
  }

  function failure(message: string) {
    setNotice(null);
    setError(message);
  }

  async function changeRole(target: SessionUser, role: Role) {
    const result = await api(`/users/${target.id}`, {
      method: "PATCH",
      csrfToken,
      body: { role },
    });
    if (!result.ok) failure(errorDetail(result.data, "Unable to update the role."));
    else feedback(`Role updated for ${target.email}.`);
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

  const showFilters = (users?.length ?? 0) > 5;

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
              placeholder="Email"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <div className="form-field">
            <label htmlFor="user-role-filter">Role</label>
            <select
              id="user-role-filter"
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value)}
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
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
        </div>
      )}

      {visible === null ? (
        <p className="page-status" role="status">
          Loading users…
        </p>
      ) : (
        <Card flush className="table-card">
          <table className="data-table">
            <caption className="visually-hidden">Existing users</caption>
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((target) => {
                const isSelf = target.id === selfId;
                return (
                  <tr key={target.id}>
                    <td style={{ fontWeight: 600 }}>
                      {target.email}
                      {isSelf && (
                        <span className="cell-secondary" style={{ fontWeight: 400 }}>
                          {" "}
                          (you)
                        </span>
                      )}
                    </td>
                    <td>
                      <label htmlFor={`role-${target.id}`} className="visually-hidden">
                        Role for {target.email}
                      </label>
                      <span
                        style={{ display: "inline-flex", gap: "0.5rem", alignItems: "center" }}
                      >
                        <Badge tone={ROLE_TONES[target.role]}>{ROLE_LABELS[target.role]}</Badge>
                        <select
                          id={`role-${target.id}`}
                          value={target.role}
                          disabled={isSelf}
                          title={isSelf ? "You cannot change your own role." : undefined}
                          onChange={(event) =>
                            void changeRole(target, event.target.value as Role)
                          }
                        >
                          {Object.entries(ROLE_LABELS).map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </span>
                    </td>
                    <td>
                      {target.is_active ? (
                        <Badge tone="green">Active</Badge>
                      ) : (
                        <Badge>Inactive</Badge>
                      )}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <ActionsMenu label="Actions">
                        {(close) => (
                          <>
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
        open={resetting !== null}
        title={`Set a temporary password`}
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
