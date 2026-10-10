"use client";
import { Toggle } from "./ui";

export type SchemaField = { key: string; label: string; type: string; default: any; min?: number; max?: number; step?: number;
  options?: any[]; help?: string; group?: string; editable?: boolean; admin_only?: boolean; pattern?: string };

/** Renders a bot's configuration schema (from the bot version) as a form. Validation happens on the server. */
export default function ConfigForm({ schema, value, onChange, asAdmin = false, disabled = false }:
  { schema: { fields: SchemaField[] } | null; value: Record<string, any>; onChange: (v: Record<string, any>) => void; asAdmin?: boolean; disabled?: boolean }) {
  if (!schema?.fields?.length) return <p className="mu">This bot has no configurable settings here.</p>;
  const groups = Array.from(new Set(schema.fields.map((f) => f.group || "General")));
  const set = (k: string, v: any) => onChange({ ...value, [k]: v });
  return (
    <div className="flex flex-col gap-4">
      {groups.map((g) => (
        <fieldset key={g} className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
          <legend className="mb-1.5 text-[11px] font-bold uppercase tracking-wide text-muted">{g}</legend>
          {schema.fields.filter((f) => (f.group || "General") === g).map((f) => {
            const locked = disabled || f.editable === false || (f.admin_only && !asAdmin);
            const v = value[f.key] ?? f.default;
            const id = `cfg-${f.key}`;
            return (
              <div key={f.key} className="field">
                <label htmlFor={id}>{f.label}{locked && <span className="text-faint">{f.admin_only ? "admin only" : f.editable === false ? "fixed" : ""}</span>}</label>
                {f.type === "boolean" ? <div className="flex items-center gap-2"><Toggle on={!!v} onChange={(x) => !locked && set(f.key, x)} label={f.label} /><span className="mu text-xs">{v ? "On" : "Off"}</span></div>
                  : f.type === "enum" ? <select id={id} className="inp" disabled={locked} value={v} onChange={(e) => set(f.key, e.target.value)}>{(f.options || []).map((o) => <option key={String(o)}>{String(o)}</option>)}</select>
                  : f.type === "string" ? <input id={id} className="inp mono" disabled={locked} value={v ?? ""} onChange={(e) => set(f.key, e.target.value)} />
                  : <input id={id} className="inp mono" type="number" disabled={locked} min={f.min} max={f.max} step={f.step ?? (f.type === "integer" ? 1 : "any")}
                      value={v ?? ""} onChange={(e) => set(f.key, e.target.value === "" ? "" : Number(e.target.value))} />}
                <span className="mu text-[11px]">{f.help}{f.min !== undefined && f.type !== "boolean" ? ` Range ${f.min}–${f.max}.` : ""}</span>
              </div>
            );
          })}
        </fieldset>
      ))}
    </div>
  );
}
