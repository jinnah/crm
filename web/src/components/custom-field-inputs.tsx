"use client";

import type { CustomField } from "@/lib/api";

type Props = {
  fields: CustomField[];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
};

/** Renders active custom-field definitions as typed, labeled inputs. */
export function CustomFieldInputs({ fields, values, onChange }: Props) {
  if (fields.length === 0) return null;
  return (
    <>
      {fields.map((field) => {
        const id = `custom-${field.key}`;
        const raw = values[field.key];
        if (field.type === "boolean") {
          return (
            <div className="form-field form-field-checkbox" key={field.key}>
              <label htmlFor={id}>
                <input
                  id={id}
                  type="checkbox"
                  checked={raw === true}
                  onChange={(event) => onChange(field.key, event.target.checked)}
                />{" "}
                {field.label}
                {field.required ? " *" : ""}
              </label>
            </div>
          );
        }
        return (
          <div className="form-field" key={field.key}>
            <label htmlFor={id}>
              {field.label}
              {field.required ? " *" : ""}
            </label>
            {field.type === "select" ? (
              <select
                id={id}
                value={typeof raw === "string" ? raw : ""}
                onChange={(event) => onChange(field.key, event.target.value || null)}
              >
                <option value="">—</option>
                {(field.options ?? []).map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                type={field.type === "number" ? "number" : field.type === "date" ? "date" : "text"}
                value={raw === null || raw === undefined ? "" : String(raw)}
                onChange={(event) => {
                  const text = event.target.value;
                  if (text === "") {
                    onChange(field.key, null);
                  } else if (field.type === "number") {
                    onChange(field.key, Number(text));
                  } else {
                    onChange(field.key, text);
                  }
                }}
              />
            )}
          </div>
        );
      })}
    </>
  );
}
