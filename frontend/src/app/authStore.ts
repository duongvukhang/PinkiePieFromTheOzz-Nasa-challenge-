import { create } from "zustand";

export interface AuthUser {
  id?: string;
  name: string;
  email: string;
  token?: string;
}

const STORAGE_KEY = "skytrace_session_user";

function withStorage<T>(fn: (s: Storage) => T, fallback: T): T {
  try {
    return typeof window !== "undefined" ? fn(window.localStorage) : fallback;
  } catch {
    return fallback;
  }
}

interface AuthStore {
  user: AuthUser | null;
  login: (user: AuthUser) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthStore>((set) => ({
  user: withStorage((s) => {
    const raw = s.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  }, null),
  login: (user) => {
    withStorage((s) => s.setItem(STORAGE_KEY, JSON.stringify(user)), undefined);
    set({ user });
  },
  logout: () => {
    withStorage((s) => s.removeItem(STORAGE_KEY), undefined);
    set({ user: null });
  },
}));
