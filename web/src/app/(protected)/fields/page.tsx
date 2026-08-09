"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import { api, errorDetail, type CustomField } from "@/lib/api";

const FIELD_TYPES = ["text", "number", "date", "boolean", "select"] as const;

export default function FieldsPage() {
  const { user, csrfToken } = useAuth();
  const [fields, setFields] = useState<CustomField[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  const isOwner = user.role === "owner";

  useEffect(() => {
    if (!isOwner) return;
    let cancelled = false;
    void api<CustomField[]>("/custom-fields?include_inactive=true").then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load custom fields."));
        return;
      }
      setFields(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [isOwner, reloadNonce]);

  if (!isOwner) {
    return (
      <section>
        <h1>Custom fields</h1>
        <p className="form-error" role="alert">
          You do not have access to custom-field management.
        </p>
      </section>
    );
  }

  async function patchField(field: CustomField, changes: Record<string, unknown>) {
    setError(null);
    setNotice(null);
    const result = await api(`/custom-fields/${field.id}`, {
      method: "PATCH",
      csrfToken,
      body: changes,
    });
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to update the field."));
      return;
    }
    setNotice(`Field "${field.label}" updated.`);
    setReloadNonce((value) => value + 1);
  }

  return (
    <section>
      <h1>Custom fields</h1>
      <p>
        Fields appear on lead screens. Deactivating a field hides it from forms but keeps
        stored values.
      </p>
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
      {fields === null ? (
        <p className="page-status" role="status">
          Loading fields…
        </p>
      ) : fields.length === 0 ? (
        <p>No custom fields defined yet.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Label</th>
              <th scope="col">Key</th>
              <th scope="col">Type</th>
              <th scope="col">Required</th>
              <th scope="col">Active</th>
              <th scope="col">Order</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((field) => (
              <tr key={field.id}>
                <td>{field.label}</td>
                <td>
                  <code>{field.key}</code>
                </td>
                <td>
                  {field.type}
                  {field.options !== null && ` (${field.options.join(", ")})`}
                </td>
                <td>
                  <button
                    type="button"
                    onClick={() => void patchField(field, { required: !field.required })}
                  >
                    {field.required ? "Required" : "Optional"}
                  </button>
                </td>
                <td>
                  <button
                    type="button"
                    onClick={() => void patchField(field, { is_active: !field.is_active })}
                  >
                    {field.is_active ? "Active" : "Inactive"}
                  </button>
                </td>
                <td>{field.display_order}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <CreateFieldForm
        csrfToken={csrfToken}
        onCreated={(label) => {
          setError(null);
          setNotice(`Field "${label}" created.`);
          setReloadNonce((value) => value + 1);
        }}
      />
    </section>
  );
}

function CreateFieldForm({
  csrfToken,
  onCreated,
}: {
  csrfToken: string;
  onCreated: (label: string) => void;
}) {
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const [type, setType] = useState<(typeof FIELD_TYPES)[number]>("text");
  const [options, setOptions] = useState("");
  const [required, setRequired] = useState(false);
  const [order, setOrder] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const result = await api("/custom-fields", {
      method: "POST",
      csrfToken,
      body: {
        label,
        key,
        type,
        options:
          type === "select"
            ? options
                .split(",")
                .map((option) => option.trim())
                .filter(Boolean)
            : null,
        required,
        display_order: order,
      },
    });
    setSubmitting(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to create the field."));
      return;
    }
    const created = label;
    setLabel("");
    setKey("");
    setOptions("");
    setRequired(false);
    setOrder(0);
    onCreated(created);
  }

  return (
    <form className="narrow-form" onSubmit={handleSubmit} noValidate>
      <h2>New field</h2>
      <div className="form-field">
        <label htmlFor="field-label">Label</label>
        <input
          id="field-label"
          required
          value={label}
          onChange={(event) => setLabel(event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="field-key">Key</label>
        <input
          id="field-key"
          required
          aria-describedby="field-key-help"
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
        <p id="field-key-help" className="form-help">
          Lowercase letters, digits and underscores. Cannot be changed later.
        </p>
      </div>
      <div className="form-field">
        <label htmlFor="field-type">Type</label>
        <select
          id="field-type"
          value={type}
          onChange={(event) => setType(event.target.value as (typeof FIELD_TYPES)[number])}
        >
          {FIELD_TYPES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>
      {type === "select" && (
        <div className="form-field">
          <label htmlFor="field-options">Options (comma-separated)</label>
          <input
            id="field-options"
            value={options}
            onChange={(event) => setOptions(event.target.value)}
          />
        </div>
      )}
      <div className="form-field form-field-checkbox">
        <label htmlFor="field-required">
          <input
            id="field-required"
            type="checkbox"
            checked={required}
            onChange={(event) => setRequired(event.target.checked)}
          />{" "}
          Required
        </label>
      </div>
      <div className="form-field">
        <label htmlFor="field-order">Display order</label>
        <input
          id="field-order"
          type="number"
          value={order}
          onChange={(event) => setOrder(Number(event.target.value))}
        />
      </div>
      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" disabled={submitting}>
        {submitting ? "Creating…" : "Create field"}
      </button>
    </form>
  );
}
