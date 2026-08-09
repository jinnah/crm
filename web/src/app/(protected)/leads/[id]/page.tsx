"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import { CustomFieldInputs } from "@/components/custom-field-inputs";
import { LeadBadges, sourceLabel, statusLabel } from "@/components/lead-badges";
import { ResponseBadge } from "@/components/response-badge";
import { SmsPanel } from "@/components/sms-panel";
import {
  api,
  errorDetail,
  LEAD_SOURCES,
  LEAD_STATUSES,
  type Activity,
  type AssignableUser,
  type CustomField,
  type Lead,
} from "@/lib/api";
import { formatDateTime, fromLocalInputValue, toLocalInputValue } from "@/lib/datetime";

export default function LeadDetailPage() {
  const params = useParams<{ id: string }>();
  const leadId = params.id;
  const { user, csrfToken } = useAuth();
  const canManage = user.role === "owner" || user.role === "manager";

  const [lead, setLead] = useState<Lead | null>(null);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [fields, setFields] = useState<CustomField[]>([]);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void api<Lead>(`/leads/${leadId}`).then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setLoadError(errorDetail(result.data, "Lead not found."));
        return;
      }
      setLead(result.data);
    });
    void api<Activity[]>(`/leads/${leadId}/activities`).then((result) => {
      if (!cancelled && result.ok && result.data !== null) setActivities(result.data);
    });
    void api<CustomField[]>("/custom-fields").then((result) => {
      if (!cancelled && result.ok && result.data !== null) setFields(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [leadId, reloadNonce]);

  useEffect(() => {
    if (!canManage) return;
    let cancelled = false;
    void api<AssignableUser[]>("/leads/assignable-users").then((result) => {
      if (!cancelled && result.ok && result.data !== null) setUsers(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [canManage]);

  const reload = useCallback(() => setReloadNonce((value) => value + 1), []);

  const runAction = useCallback(
    async (path: string, body?: unknown, method: "POST" | "PATCH" = "POST") => {
      setActionError(null);
      setNotice(null);
      const result = await api(path, { method, csrfToken, body });
      if (!result.ok) {
        setActionError(errorDetail(result.data, "The action failed. Please try again."));
        return false;
      }
      reload();
      return true;
    },
    [csrfToken, reload],
  );

  if (loadError !== null) {
    return (
      <section>
        <h1>Lead</h1>
        <p className="form-error" role="alert">
          {loadError}
        </p>
      </section>
    );
  }
  if (lead === null) {
    return (
      <p className="page-status" role="status">
        Loading lead…
      </p>
    );
  }

  const archived = lead.archived_at !== null;

  return (
    <section>
      <div className="page-head">
        <h1>
          {lead.name || "Unnamed lead"} <LeadBadges lead={lead} /> <ResponseBadge lead={lead} />
        </h1>
        {canManage &&
          (archived ? (
            <button
              type="button"
              onClick={() => {
                void runAction(`/leads/${lead.id}/restore`).then(
                  (ok) => ok && setNotice("Lead restored."),
                );
              }}
            >
              Restore
            </button>
          ) : (
            <button
              type="button"
              onClick={() => {
                void runAction(`/leads/${lead.id}/archive`).then(
                  (ok) => ok && setNotice("Lead archived. History is preserved."),
                );
              }}
            >
              Archive
            </button>
          ))}
      </div>

      {actionError !== null && (
        <p className="form-error" role="alert">
          {actionError}
        </p>
      )}
      {notice !== null && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
      {archived && (
        <p>
          This lead is archived. Restore it to make changes; its history remains
          intact.
        </p>
      )}

      {lead.first_inbound_at !== null && lead.first_response_at === null && !archived && (
        <p className="button-row">
          <button
            type="button"
            onClick={() => {
              void runAction(`/leads/${lead.id}/mark-contacted`).then(
                (ok) => ok && setNotice("Recorded as contacted outside the CRM."),
              );
            }}
          >
            Mark contacted outside CRM
          </button>
        </p>
      )}

      <div className="lead-columns">
        <div>
          <DetailsForm
            key={`details-${lead.updated_at}`}
            lead={lead}
            fields={fields}
            canManage={canManage}
            disabled={archived}
            onSave={(changes) =>
              runAction(`/leads/${lead.id}`, changes, "PATCH").then(
                (ok) => ok && setNotice("Lead updated."),
              )
            }
          />

          {canManage && !archived && (
            <div className="form-field">
              <label htmlFor="assignee-select">Assignee</label>
              <select
                id="assignee-select"
                value={lead.assigned_to ?? ""}
                onChange={(event) => {
                  void runAction(`/leads/${lead.id}/assign`, {
                    user_id: event.target.value === "" ? null : event.target.value,
                  }).then((ok) => ok && setNotice("Assignment updated."));
                }}
              >
                <option value="">Unassigned</option>
                {users.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.email}
                  </option>
                ))}
              </select>
            </div>
          )}

          {!archived && (
            <FollowUpControls
              key={`follow-up-${lead.updated_at}`}
              lead={lead}
              onSave={(iso) =>
                runAction(`/leads/${lead.id}`, { next_follow_up_at: iso }, "PATCH").then(
                  (ok) => ok && setNotice("Follow-up updated."),
                )
              }
              onComplete={() =>
                runAction(`/leads/${lead.id}/complete-follow-up`).then(
                  (ok) => ok && setNotice("Follow-up completed."),
                )
              }
            />
          )}
          <p>
            Last contacted: {formatDateTime(lead.last_contacted_at)}
            <br />
            Created: {formatDateTime(lead.created_at)}
          </p>
        </div>

        <div>
          {!archived && (
            <NoteForm
              onSubmit={(content) =>
                runAction(`/leads/${lead.id}/notes`, { content }).then(
                  (ok) => ok && setNotice("Note added."),
                )
              }
            />
          )}
          <SmsPanel
            lead={lead}
            activities={activities}
            csrfToken={csrfToken}
            canSend={canManage || lead.assigned_to === user.id}
            onChanged={reload}
          />
          <Timeline activities={activities} />
        </div>
      </div>
    </section>
  );
}

function DetailsForm({
  lead,
  fields,
  canManage,
  disabled,
  onSave,
}: {
  lead: Lead;
  fields: CustomField[];
  canManage: boolean;
  disabled: boolean;
  onSave: (changes: Record<string, unknown>) => Promise<unknown>;
}) {
  const [name, setName] = useState(lead.name);
  const [email, setEmail] = useState(lead.email ?? "");
  const [phone, setPhone] = useState(lead.phone ?? "");
  const [company, setCompany] = useState(lead.company);
  const [status, setStatus] = useState(lead.status);
  const [source, setSource] = useState(lead.source);
  const [customValues, setCustomValues] = useState<Record<string, unknown>>(lead.custom_values);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    const changes: Record<string, unknown> = { status, custom_values: customValues };
    if (canManage) {
      changes.name = name;
      changes.email = email.trim() === "" ? null : email.trim();
      changes.phone = phone.trim() === "" ? null : phone.trim();
      changes.company = company;
      changes.source = source;
    }
    await onSave(changes);
    setSaving(false);
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <h2>Details</h2>
      {canManage && (
        <>
          <div className="form-field">
            <label htmlFor="detail-name">Name</label>
            <input
              id="detail-name"
              value={name}
              disabled={disabled}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="form-field">
            <label htmlFor="detail-email">Email</label>
            <input
              id="detail-email"
              type="email"
              value={email}
              disabled={disabled}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div className="form-field">
            <label htmlFor="detail-phone">Phone</label>
            <input
              id="detail-phone"
              type="tel"
              value={phone}
              disabled={disabled}
              onChange={(event) => setPhone(event.target.value)}
            />
          </div>
          <div className="form-field">
            <label htmlFor="detail-company">Company</label>
            <input
              id="detail-company"
              value={company}
              disabled={disabled}
              onChange={(event) => setCompany(event.target.value)}
            />
          </div>
          <div className="form-field">
            <label htmlFor="detail-source">Source</label>
            <select
              id="detail-source"
              value={source}
              disabled={disabled}
              onChange={(event) => setSource(event.target.value)}
            >
              {LEAD_SOURCES.map((value) => (
                <option key={value} value={value}>
                  {sourceLabel(value)}
                </option>
              ))}
            </select>
          </div>
        </>
      )}
      {!canManage && (
        <p>
          {lead.email ?? "No email"} · {lead.phone ?? "No phone"}
          {lead.company && ` · ${lead.company}`}
        </p>
      )}
      <div className="form-field">
        <label htmlFor="detail-status">Status</label>
        <select
          id="detail-status"
          value={status}
          disabled={disabled}
          onChange={(event) => setStatus(event.target.value)}
        >
          {LEAD_STATUSES.map((value) => (
            <option key={value} value={value}>
              {statusLabel(value)}
            </option>
          ))}
        </select>
      </div>
      <CustomFieldInputs
        fields={fields}
        values={customValues}
        onChange={(key, value) => setCustomValues((prev) => ({ ...prev, [key]: value }))}
      />
      <button type="submit" disabled={disabled || saving}>
        {saving ? "Saving…" : "Save details"}
      </button>
    </form>
  );
}

function FollowUpControls({
  lead,
  onSave,
  onComplete,
}: {
  lead: Lead;
  onSave: (iso: string | null) => Promise<unknown>;
  onComplete: () => Promise<unknown>;
}) {
  const [value, setValue] = useState(toLocalInputValue(lead.next_follow_up_at));

  return (
    <div className="follow-up-controls">
      <h2>Follow-up</h2>
      <div className="form-field">
        <label htmlFor="follow-up-at">Next follow-up</label>
        <input
          id="follow-up-at"
          type="datetime-local"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </div>
      <div className="button-row">
        <button type="button" onClick={() => void onSave(fromLocalInputValue(value))}>
          Save follow-up
        </button>
        {lead.next_follow_up_at !== null && (
          <>
            <button type="button" onClick={() => void onComplete()}>
              Complete follow-up
            </button>
            <button type="button" onClick={() => void onSave(null)}>
              Clear
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function NoteForm({ onSubmit }: { onSubmit: (content: string) => Promise<unknown> }) {
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (content.trim() === "") return;
    setSubmitting(true);
    await onSubmit(content.trim());
    setSubmitting(false);
    setContent("");
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <h2>Add note</h2>
      <div className="form-field">
        <label htmlFor="note-content">Internal note</label>
        <textarea
          id="note-content"
          rows={3}
          value={content}
          onChange={(event) => setContent(event.target.value)}
        />
      </div>
      <button type="submit" disabled={submitting || content.trim() === ""}>
        {submitting ? "Adding…" : "Add note"}
      </button>
    </form>
  );
}

const ACTIVITY_LABELS: Record<string, string> = {
  created: "Created",
  inbound_request: "Inbound request",
  note: "Note",
  status_change: "Status change",
  assignment_change: "Assignment",
  follow_up_scheduled: "Follow-up scheduled",
  follow_up_changed: "Follow-up changed",
  follow_up_completed: "Follow-up completed",
  follow_up_cleared: "Follow-up cleared",
  archived: "Archived",
  restored: "Restored",
};

function Timeline({ activities }: { activities: Activity[] }) {
  return (
    <div className="timeline">
      <h2>Timeline</h2>
      {activities.length === 0 ? (
        <p>No activity yet.</p>
      ) : (
        <ul className="timeline-list">
          {activities.map((activity) => (
            <li key={activity.id} className="timeline-item">
              <div className="timeline-meta">
                <strong>{ACTIVITY_LABELS[activity.type] ?? activity.type}</strong>
                {activity.channel !== null && <span> · {sourceLabel(activity.channel)}</span>}
                {activity.created_by_email !== null && (
                  <span> · {activity.created_by_email}</span>
                )}
                <span> · {formatDateTime(activity.occurred_at ?? activity.created_at)}</span>
              </div>
              <p className="timeline-content">{activity.content}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
