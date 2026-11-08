"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";

export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export interface ThemeContextValue {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const THEME_STORAGE_KEY = "orderbridge-theme";
const DEFAULT_THEME: Theme = "light";
const DARK_QUERY = "(prefers-color-scheme: dark)";

const ThemeContext = createContext<ThemeContextValue | null>(null);

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark" || value === "system";
}

// The chosen theme lives in localStorage (an external store), so it is read
// with useSyncExternalStore instead of being copied into state in an
// effect. When storage is blocked (private browsing), the choice is kept
// in memory for the session.
let memoryTheme: Theme = DEFAULT_THEME;
const themeListeners = new Set<() => void>();

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(stored) ? stored : memoryTheme;
  } catch {
    return memoryTheme;
  }
}

function writeTheme(theme: Theme): void {
  memoryTheme = theme;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Ignore write errors in restricted environments; memory still holds it.
  }
  themeListeners.forEach((listener) => listener());
}

function subscribeTheme(listener: () => void): () => void {
  themeListeners.add(listener);
  // Another tab changed the theme.
  window.addEventListener("storage", listener);
  return () => {
    themeListeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function subscribeSystem(listener: () => void): () => void {
  const query = window.matchMedia(DARK_QUERY);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

function systemPrefersDark(): boolean {
  return window.matchMedia(DARK_QUERY).matches;
}

function applyThemeClass(resolved: ResolvedTheme): void {
  document.documentElement.classList.toggle("dark", resolved === "dark");
}

/**
 * Provides theme state, system preference synchronization, and localStorage
 * persistence. Light is the default per project requirements; the server
 * render always uses it, and ThemeScript applies the stored theme before
 * paint so there is no flash.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => DEFAULT_THEME);
  const prefersDark = useSyncExternalStore(subscribeSystem, systemPrefersDark, () => false);
  const resolvedTheme: ResolvedTheme =
    theme === "system" ? (prefersDark ? "dark" : "light") : theme;

  // Keep the <html> class in step: an effect that updates the DOM only.
  useEffect(() => {
    applyThemeClass(resolvedTheme);
  }, [resolvedTheme]);

  const setTheme = useCallback((nextTheme: Theme) => {
    writeTheme(nextTheme);
  }, []);

  const value = useMemo(
    () => ({ theme, resolvedTheme, setTheme }),
    [theme, resolvedTheme, setTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return context;
}

/**
 * Injected into <head> to synchronously apply the theme class before browser paint.
 * Prevents flash of unstyled theme (FOUT) while strictly enforcing Light as default.
 */
export function ThemeScript() {
  const scriptContent = `(function(){try{var s=localStorage.getItem("${THEME_STORAGE_KEY}");var t=s==="dark"||s==="system"?s:"light";var isDark=t==="dark"||(t==="system"&&window.matchMedia("(prefers-color-scheme:dark)").matches);if(isDark){document.documentElement.classList.add("dark");}else{document.documentElement.classList.remove("dark");}}catch(e){}})();`;

  return <script dangerouslySetInnerHTML={{ __html: scriptContent }} />;
}
