"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import { CustomFieldInputs } from "@/components/custom-field-inputs";
import { sourceLabel, statusLabel } from "@/components/lead-badges";
import {
  api,
  errorDetail,
  LEAD_SOURCES,
  LEAD_STATUSES,
  type AssignableUser,
  type CustomField,
  type Lead,
} from "@/lib/api";
import { fromLocalInputValue } from "@/lib/datetime";

export default function NewLeadPage() {
  const router = useRouter();
  const { user, csrfToken } = useAuth();
  const canManage = user.role === "owner" || user.role === "manager";

  const [fields, setFields] = useState<CustomField[]>([]);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [company, setCompany] = useState("");
  const [status, setStatus] = useState("new");
  const [source, setSource] = useState("manual");
  const [assignee, setAssignee] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [customValues, setCustomValues] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!canManage) return;
    let cancelled = false;
    void api<CustomField[]>("/custom-fields").then((result) => {
      if (!cancelled && result.ok && result.data !== null) setFields(result.data);
    });
    void api<AssignableUser[]>("/leads/assignable-users").then((result) => {
      if (!cancelled && result.ok && result.data !== null) setUsers(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [canManage]);

  if (!canManage) {
    return (
      <section>
        <h1>New lead</h1>
        <p className="form-error" role="alert">
          You are not allowed to create leads.
        </p>
      </section>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const cleanedValues = Object.fromEntries(
      Object.entries(customValues).filter(([, value]) => value !== null && value !== ""),
    );
    const result = await api<Lead>("/leads", {
      method: "POST",
      csrfToken,
      body: {
        name,
        email: email.trim() === "" ? null : email.trim(),
        phone: phone.trim() === "" ? null : phone.trim(),
        company,
        status,
        source,
        assigned_to: assignee === "" ? null : assignee,
        next_follow_up_at: fromLocalInputValue(followUp),
        custom_values: cleanedValues,
      },
    });
    setSubmitting(false);
    if (!result.ok || result.data === null) {
      setError(errorDetail(result.data, "Unable to create the lead."));
      return;
    }
    router.push(`/leads/${result.data.id}`);
  }

  return (
    <section className="narrow-form">
      <h1>New lead</h1>
      <form onSubmit={handleSubmit} noValidate>
        <div className="form-field">
          <label htmlFor="lead-name">Name</label>
          <input
            id="lead-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="lead-email">Email</label>
          <input
            id="lead-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="lead-phone">Phone</label>
          <input
            id="lead-phone"
            type="tel"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="lead-company">Company</label>
          <input
            id="lead-company"
            value={company}
            onChange={(event) => setCompany(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="lead-status">Status</label>
          <select
            id="lead-status"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            {LEAD_STATUSES.map((value) => (
              <option key={value} value={value}>
                {statusLabel(value)}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label htmlFor="lead-source">Source</label>
          <select
            id="lead-source"
            value={source}
            onChange={(event) => setSource(event.target.value)}
          >
            {LEAD_SOURCES.map((value) => (
              <option key={value} value={value}>
                {sourceLabel(value)}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label htmlFor="lead-assignee">Assignee</label>
          <select
            id="lead-assignee"
            value={assignee}
            onChange={(event) => setAssignee(event.target.value)}
          >
            <option value="">Unassigned</option>
            {users.map((option) => (
              <option key={option.id} value={option.id}>
                {option.email}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label htmlFor="lead-follow-up">Next follow-up</label>
          <input
            id="lead-follow-up"
            type="datetime-local"
            value={followUp}
            onChange={(event) => setFollowUp(event.target.value)}
          />
        </div>
        <CustomFieldInputs
          fields={fields}
          values={customValues}
          onChange={(key, value) => setCustomValues((prev) => ({ ...prev, [key]: value }))}
        />
        {error !== null && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={submitting}>
          {submitting ? "Creating…" : "Create lead"}
        </button>
      </form>
    </section>
  );
}
