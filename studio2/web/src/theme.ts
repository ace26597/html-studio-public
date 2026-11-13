import { useCallback, useEffect, useState } from "react";
import { loadString, saveString } from "./storage";

export type Theme = "light" | "dark";

function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Follows the system until the user toggles; the choice is then saved. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = loadString("theme");
    return saved === "dark" || saved === "light" ? saved : systemTheme();
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    // Keep the phone's status bar / browser bar in the studio's own theme.
    const colour = theme === "dark" ? "#19191c" : "#ffffff";
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.setAttribute("content", colour));
  }, [theme]);

  useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!query) return;
    const onChange = () => {
      if (!loadString("theme")) setTheme(query.matches ? "dark" : "light");
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      saveString("theme", next);
      return next;
    });
  }, []);

  return [theme, toggle];
}
