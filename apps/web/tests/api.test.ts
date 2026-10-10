import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, logout, tokens } from "@/lib/api";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => { localStorage.clear(); });

describe("api client", () => {
  it("sends the bearer token and parses JSON", async () => {
    tokens.set({ access_token: "A1", refresh_token: "R1" });
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { ok: true }));
    expect(await api("/x")).toEqual({ ok: true });
    expect((f.mock.calls[0][1] as RequestInit).headers).toMatchObject({ Authorization: "Bearer A1" });
  });

  it("maps server errors to readable messages with code and reference", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json(422, { detail: "'risk' must be between 0.1 and 3.", code: "INVALID_CONFIG" }));
    await expect(api("/x")).rejects.toMatchObject({ status: 422, message: "'risk' must be between 0.1 and 3.", code: "INVALID_CONFIG" });
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json(422, { detail: [{ loc: ["body", "email"], msg: "not valid" }] }));
    await expect(api("/x")).rejects.toMatchObject({ message: "email: not valid" });
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json(500, { detail: "Something went wrong", code: "INTERNAL", correlation_id: "abc123" }));
    await expect(api("/x")).rejects.toMatchObject({ message: "Something went wrong (reference abc123)", correlationId: "abc123" });
  });

  it("refreshes once on 401, stores the rotated tokens and retries", async () => {
    tokens.set({ access_token: "old", refresh_token: "R1" });
    const f = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(401, { detail: "expired" }))
      .mockResolvedValueOnce(json(200, { access_token: "new", refresh_token: "R2" }))
      .mockResolvedValueOnce(json(200, { ok: 1 }));
    expect(await api("/x")).toEqual({ ok: 1 });
    expect(tokens.access()).toBe("new");
    expect(tokens.refresh()).toBe("R2");
    expect((f.mock.calls[2][1] as RequestInit).headers).toMatchObject({ Authorization: "Bearer new" });
  });

  it("clears tokens when the refresh is refused", async () => {
    tokens.set({ access_token: "old", refresh_token: "R1" });
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json(401, {})).mockResolvedValueOnce(json(401, {}));
    await expect(api("/x")).rejects.toBeInstanceOf(ApiError);
    expect(tokens.access()).toBeNull();
  });

  it("reports network failures clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(api("/x")).rejects.toMatchObject({ status: 0, message: expect.stringContaining("Can't reach the server") });
  });

  it("logout revokes the refresh token on the server and clears storage", async () => {
    tokens.set({ access_token: "A", refresh_token: "R9" });
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { ok: true }));
    await logout();
    expect(tokens.access()).toBeNull();
    expect(String(f.mock.calls[0][0])).toContain("/api/v1/auth/logout");
    expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toEqual({ refresh_token: "R9" });
  });
});
