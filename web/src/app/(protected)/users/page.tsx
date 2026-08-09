"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import { api, errorDetail, type Role, type SessionUser } from "@/lib/api";
import { passwordPolicyError } from "@/lib/password";
import { ROLE_LABELS } from "@/lib/roles";

export default function UsersPage() {
  const { user } = useAuth();
  if (user.role !== "owner") {
    return (
      <section>
        <h1>User management</h1>
        <p className="form-error" role="alert">
          You do not have access to user management.
        </p>
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

  const load = useCallback(async () => {
    setReloadNonce((value) => value + 1);
  }, []);

  async function changeRole(target: SessionUser, role: Role) {
    setError(null);
    setNotice(null);
    const result = await api(`/users/${target.id}`, {
      method: "PATCH",
      csrfToken,
      body: { role },
    });
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to update the role."));
    } else {
      setNotice(`Role updated for ${target.email}.`);
    }
    await load();
  }

  async function toggleActive(target: SessionUser) {
    setError(null);
    setNotice(null);
    const result = await api(`/users/${target.id}`, {
      method: "PATCH",
      csrfToken,
      body: { is_active: !target.is_active },
    });
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to update the user."));
    } else {
      setNotice(`${target.email} ${target.is_active ? "deactivated" : "reactivated"}.`);
    }
    await load();
  }

  return (
    <section>
      <h1>User management</h1>
      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice !== null && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
      {users === null ? (
        <p className="page-status" role="status">
          Loading users…
        </p>
      ) : (
        <table className="users-table">
          <caption className="visually-hidden">Existing users</caption>
          <thead>
            <tr>
              <th scope="col">Email</th>
              <th scope="col">Role</th>
              <th scope="col">Status</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((target) => (
              <UserRow
                key={target.id}
                target={target}
                isSelf={target.id === selfId}
                csrfToken={csrfToken}
                onChangeRole={changeRole}
                onToggleActive={toggleActive}
                onDone={(message) => {
                  setError(null);
                  setNotice(message);
                  void load();
                }}
                onError={(message) => {
                  setNotice(null);
                  setError(message);
                }}
              />
            ))}
          </tbody>
        </table>
      )}
      <CreateUserForm
        csrfToken={csrfToken}
        onCreated={(email) => {
          setError(null);
          setNotice(`User ${email} created with a temporary password.`);
          void load();
        }}
      />
    </section>
  );
}

type UserRowProps = {
  target: SessionUser;
  isSelf: boolean;
  csrfToken: string;
  onChangeRole: (target: SessionUser, role: Role) => Promise<void>;
  onToggleActive: (target: SessionUser) => Promise<void>;
  onDone: (message: string) => void;
  onError: (message: string) => void;
};

function UserRow({
  target,
  isSelf,
  csrfToken,
  onChangeRole,
  onToggleActive,
  onDone,
  onError,
}: UserRowProps) {
  const [showReset, setShowReset] = useState(false);
  const [tempPassword, setTempPassword] = useState("");

  async function submitReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const policyError = passwordPolicyError(tempPassword);
    if (policyError !== null) {
      onError(policyError);
      return;
    }
    const result = await api(`/users/${target.id}/reset-password`, {
      method: "POST",
      csrfToken,
      body: { temporary_password: tempPassword },
    });
    if (!result.ok) {
      onError(errorDetail(result.data, "Unable to set a temporary password."));
      return;
    }
    setTempPassword("");
    setShowReset(false);
    onDone(`Temporary password set for ${target.email}; change required at next login.`);
  }

  const roleSelectId = `role-${target.id}`;
  const resetInputId = `temp-password-${target.id}`;

  return (
    <tr>
      <td>
        {target.email}
        {isSelf && " (you)"}
      </td>
      <td>
        <label htmlFor={roleSelectId} className="visually-hidden">
          Role for {target.email}
        </label>
        <select
          id={roleSelectId}
          value={target.role}
          onChange={(event) => void onChangeRole(target, event.target.value as Role)}
        >
          {Object.entries(ROLE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </td>
      <td>{target.is_active ? "Active" : "Inactive"}</td>
      <td className="users-actions">
        <button type="button" onClick={() => void onToggleActive(target)}>
          {target.is_active ? "Deactivate" : "Reactivate"}
        </button>
        <button type="button" onClick={() => setShowReset((value) => !value)}>
          {showReset ? "Cancel reset" : "Set temporary password"}
        </button>
        {showReset && (
          <form className="inline-form" onSubmit={submitReset}>
            <label htmlFor={resetInputId}>Temporary password for {target.email}</label>
            <input
              id={resetInputId}
              type="password"
              autoComplete="new-password"
              required
              value={tempPassword}
              onChange={(event) => setTempPassword(event.target.value)}
            />
            <button type="submit">Apply</button>
          </form>
        )}
      </td>
    </tr>
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
    <form className="create-user-form" onSubmit={handleSubmit} noValidate>
      <h2>Create user</h2>
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
      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" disabled={submitting}>
        {submitting ? "Creating…" : "Create user"}
      </button>
    </form>
  );
}
