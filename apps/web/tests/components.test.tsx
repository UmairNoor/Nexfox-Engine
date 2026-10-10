import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConfigForm from "@/components/ConfigForm";
import { ConfirmModal, StatusPill } from "@/components/ui";

afterEach(cleanup);

const schema = {
  fields: [
    { key: "symbol", label: "Broker symbol", type: "string", default: "GOLD", group: "Account" },
    { key: "magic_number", label: "Magic number", type: "integer", default: 88880001, group: "Account", admin_only: true },
    { key: "risk_percent", label: "Risk per setup", type: "number", default: 1, min: 0.1, max: 3, step: 0.1, group: "Risk" },
    { key: "lot_step", label: "Lot step", type: "number", default: 0.01, group: "Risk", editable: false },
    { key: "dry_run", label: "Dry run", type: "boolean", default: false, group: "Runtime" },
  ],
};

describe("ConfigForm", () => {
  it("groups fields and locks admin-only / fixed settings for customers", () => {
    render(<ConfigForm schema={schema} value={{}} onChange={() => {}} />);
    expect(screen.getByText("Account")).toBeTruthy();
    expect(screen.getByText("Risk")).toBeTruthy();
    expect((screen.getByLabelText(/Magic number/) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText(/Lot step/) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText(/Risk per setup/) as HTMLInputElement).disabled).toBe(false);
    expect(screen.getByText(/Range 0.1–3/)).toBeTruthy();
  });

  it("unlocks admin-only fields for admins and reports typed changes", () => {
    const onChange = vi.fn();
    render(<ConfigForm schema={schema} value={{ risk_percent: 1 }} onChange={onChange} asAdmin />);
    expect((screen.getByLabelText(/Magic number/) as HTMLInputElement).disabled).toBe(false);
    fireEvent.change(screen.getByLabelText(/Risk per setup/), { target: { value: "0.5" } });
    expect(onChange).toHaveBeenLastCalledWith({ risk_percent: 0.5 });
    fireEvent.click(screen.getByRole("button", { name: "Dry run" }));
    expect(onChange).toHaveBeenLastCalledWith({ risk_percent: 1, dry_run: true });
  });

  it("shows an empty state without a schema", () => {
    render(<ConfigForm schema={null} value={{}} onChange={() => {}} />);
    expect(screen.getByText(/no configurable settings/)).toBeTruthy();
  });
});

describe("ConfirmModal", () => {
  it("keeps the action disabled until the phrase and reason are given", () => {
    const onConfirm = vi.fn();
    render(<ConfirmModal open onClose={() => {}} title="Halt all trading?" body="..." phrase="HALT" reason action="Halt trading" onConfirm={onConfirm} />);
    const btn = screen.getByRole("button", { name: "Halt trading" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Type/), { target: { value: "halt" } });
    expect(btn.disabled).toBe(true); // reason still missing
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "incident" } });
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledWith("incident");
  });

  it("renders nothing when closed", () => {
    render(<ConfirmModal open={false} onClose={() => {}} title="X" body="" action="Go" onConfirm={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("StatusPill", () => {
  it("maps platform states to readable labels", () => {
    render(<><StatusPill status="auth_failed" /><StatusPill status="uncertain" /><StatusPill status="unverified" /><StatusPill status="something_new" /></>);
    expect(screen.getByText("Login failed")).toBeTruthy();
    expect(screen.getByText("Uncertain")).toBeTruthy();
    expect(screen.getByText("Not verified")).toBeTruthy();
    expect(screen.getByText("something_new")).toBeTruthy();
  });
});
