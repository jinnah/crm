"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import { SchedulingSettingsForm } from "@/components/scheduling-settings-form";
import { api, errorDetail, type CommunicationSettings } from "@/lib/api";

const TEMPLATE_VARIABLES = ["lead_name", "business_name", "source", "lead_id"];

export default function CommunicationSettingsPage() {
  const { user, csrfToken } = useAuth();
  const isOwner = user.role === "owner";

  const [settings, setSettings] = useState<CommunicationSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isOwner) return;
    let cancelled = false;
    void api<CommunicationSettings>("/settings/communication").then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load settings."));
        return;
      }
      setSettings(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [isOwner]);

  if (!isOwner) {
    return (
      <section>
        <h1>Communication settings</h1>
        <p className="form-error" role="alert">
          You do not have access to communication settings.
        </p>
      </section>
    );
  }

  function update<K extends keyof CommunicationSettings>(
    key: K,
    value: CommunicationSettings[K],
  ) {
    setSettings((previous) => (previous === null ? previous : { ...previous, [key]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (settings === null) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    const result = await api<CommunicationSettings>("/settings/communication", {
      method: "PATCH",
      csrfToken,
      body: settings,
    });
    setSaving(false);
    if (!result.ok || result.data === null) {
      setError(errorDetail(result.data, "Unable to save settings."));
      return;
    }
    setSettings(result.data);
    setNotice("Settings saved.");
  }

  if (settings === null) {
    return (
      <p className="page-status" role="status">
        {error ?? "Loading settings…"}
      </p>
    );
  }

  return (
    <section className="narrow-form">
      <h1>Communication settings</h1>
      <form onSubmit={handleSubmit} noValidate>
        <div className="form-field">
          <label htmlFor="business-name">Business display name</label>
          <input
            id="business-name"
            value={settings.business_name}
            onChange={(event) => update("business_name", event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="form-title">Public form title</label>
          <input
            id="form-title"
            value={settings.form_title}
            onChange={(event) => update("form_title", event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="form-intro">Public form introduction</label>
          <textarea
            id="form-intro"
            rows={2}
            value={settings.form_intro}
            onChange={(event) => update("form_intro", event.target.value)}
          />
        </div>

        <h2>Automated messages</h2>
        <p className="form-help">
          Templates may use: {TEMPLATE_VARIABLES.map((name) => `{{${name}}}`).join(", ")}. Any
          other variable is rejected.
        </p>
        <div className="form-field form-field-checkbox">
          <label htmlFor="ack-enabled">
            <input
              id="ack-enabled"
              type="checkbox"
              checked={settings.acknowledgment_enabled}
              onChange={(event) => update("acknowledgment_enabled", event.target.checked)}
            />{" "}
            Send an automatic acknowledgment to new leads
          </label>
        </div>
        <div className="form-field">
          <label htmlFor="ack-template">Acknowledgment message</label>
          <textarea
            id="ack-template"
            rows={3}
            value={settings.acknowledgment_template}
            onChange={(event) => update("acknowledgment_template", event.target.value)}
          />
        </div>
        <div className="form-field form-field-checkbox">
          <label htmlFor="alert-enabled">
            <input
              id="alert-enabled"
              type="checkbox"
              checked={settings.alert_enabled}
              onChange={(event) => update("alert_enabled", event.target.checked)}
            />{" "}
            Send a new-lead alert to the business
          </label>
        </div>
        <div className="form-field">
          <label htmlFor="alert-phone">Notification destination phone</label>
          <input
            id="alert-phone"
            type="tel"
            aria-describedby="alert-phone-help"
            value={settings.alert_destination_phone ?? ""}
            onChange={(event) => update("alert_destination_phone", event.target.value)}
          />
          <p id="alert-phone-help" className="form-help">
            International format, for example +15555550123.
          </p>
        </div>
        <div className="form-field">
          <label htmlFor="alert-template">New-lead alert message</label>
          <textarea
            id="alert-template"
            rows={3}
            value={settings.alert_template}
            onChange={(event) => update("alert_template", event.target.value)}
          />
        </div>

        <h2>Response target</h2>
        <div className="form-field">
          <label htmlFor="response-target">First-response target (minutes)</label>
          <input
            id="response-target"
            type="number"
            min={1}
            value={settings.response_target_minutes}
            onChange={(event) =>
              update("response_target_minutes", Number(event.target.value) || 1)
            }
          />
        </div>

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
        <button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save settings"}
        </button>
      </form>

      <SchedulingSettingsForm csrfToken={csrfToken} />
    </section>
  );
}
