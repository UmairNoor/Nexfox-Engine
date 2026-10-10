"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, logout, tokens } from "./api";

export type Me = {
  id: number; name: string; email: string; role: string; status: string; email_verified: boolean; totp_enabled: boolean; tenant_id: number;
  subscription: { plan: string; plan_name: string; status: string } | null;
  entitlement: { valid: boolean; reason: string; trial: boolean; trial_ends_at: string | null };
};

type Ctx = { me: Me | null; loading: boolean; reload: () => Promise<void>; signIn: (t: any) => void; signOut: () => Promise<void> };
const AuthCtx = createContext<Ctx>({ me: null, loading: true, reload: async () => {}, signIn: () => {}, signOut: async () => {} });

export const isStaff = (me: Me | null) => !!me && (me.role === "admin" || me.role === "support");

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    if (!tokens.access()) { setMe(null); setLoading(false); return; }
    try { setMe(await api<Me>("/api/v1/auth/me")); } catch { setMe(null); } finally { setLoading(false); }
  }, []);
  useEffect(() => { reload(); }, [reload]);
  const signIn = (t: any) => { tokens.set(t); if (t.user) setMe(t.user); };
  const signOut = async () => { await logout(); setMe(null); };
  return <AuthCtx.Provider value={{ me, loading, reload, signIn, signOut }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
