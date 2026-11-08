"use client";

import { MonitorIcon, MoonIcon, SunIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useHydrated } from "@/lib/use-hydrated";
import { cn } from "@/lib/utils";
import { useTheme, type Theme } from "@/providers/theme-provider";

interface ThemeToggleProps {
  className?: string;
  /** Display variant: "cycle" icon button or "segmented" tabs */
  variant?: "cycle" | "segmented";
}

/**
 * Accessible theme toggle supporting Light, Dark, and System modes.
 * Provides instant feedback, high-contrast focus rings, and hydration safety.
 */
export function ThemeToggle({ className, variant = "cycle" }: ThemeToggleProps) {
  const { theme, resolvedTheme, setTheme } = useTheme();
  // The stored theme is only known in the browser.
  const mounted = useHydrated();

  if (!mounted) {
    // Render static accessible placeholder during SSR / hydration to prevent layout shift
    return (
      <Button
        variant="ghost"
        size="icon"
        className={cn("size-9 shrink-0 text-muted-foreground", className)}
        aria-label="Toggle theme"
        disabled
      >
        <SunIcon className="size-4" aria-hidden="true" />
      </Button>
    );
  }

  if (variant === "segmented") {
    const options: { value: Theme; label: string; icon: typeof SunIcon }[] = [
      { value: "light", label: "Light", icon: SunIcon },
      { value: "dark", label: "Dark", icon: MoonIcon },
      { value: "system", label: "System", icon: MonitorIcon },
    ];

    return (
      <div
        role="radiogroup"
        aria-label="Theme selection"
        className={cn(
          "inline-flex items-center rounded-lg border bg-secondary/50 p-1 text-muted-foreground",
          className,
        )}
      >
        {options.map(({ value, label, icon: Icon }) => {
          const isSelected = theme === value;
          return (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={isSelected}
              tabIndex={isSelected ? 0 : -1}
              onClick={() => setTheme(value)}
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
                isSelected
                  ? "bg-card text-foreground shadow-xs"
                  : "hover:text-foreground",
              )}
            >
              <Icon className="size-3.5" aria-hidden="true" />
              <span>{label}</span>
            </button>
          );
        })}
      </div>
    );
  }

  // Cycle variant: Light -> Dark -> System -> Light
  const cycleTheme = () => {
    if (theme === "light") setTheme("dark");
    else if (theme === "dark") setTheme("system");
    else setTheme("light");
  };

  const nextLabel =
    theme === "light"
      ? "Switch to dark theme"
      : theme === "dark"
        ? "Switch to system theme"
        : "Switch to light theme";

  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn("size-9 shrink-0 text-muted-foreground hover:text-foreground", className)}
      aria-label={`${nextLabel} (currently ${theme} theme)`}
      title={`${nextLabel} (current: ${theme})`}
      onClick={cycleTheme}
    >
      {resolvedTheme === "dark" ? (
        <MoonIcon className="size-4 text-primary" aria-hidden="true" />
      ) : (
        <SunIcon className="size-4 text-primary" aria-hidden="true" />
      )}
      <span className="sr-only">Toggle theme</span>
    </Button>
  );
}
