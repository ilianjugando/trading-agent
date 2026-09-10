import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";
const KEY = "trading-agent-theme";

function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem(KEY) as Theme) || "system");

  useEffect(() => {
    apply(theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      // localStorage puede fallar en un perfil privado -- el toggle sigue
      // funcionando para esta sesion, simplemente no persiste.
    }
  }, [theme]);

  const next: Record<Theme, Theme> = { system: "dark", dark: "light", light: "system" };
  const icon = { system: "🖥", dark: "🌙", light: "☀" }[theme];
  const label = { system: "Sistema", dark: "Oscuro", light: "Claro" }[theme];

  return (
    <button
      type="button"
      onClick={() => setTheme(next[theme])}
      title={`Tema: ${label} (clic para cambiar)`}
      className="cursor-pointer rounded-md border border-border px-2.5 py-1.5 font-mono text-xs text-muted transition-colors hover:border-glow hover:text-fg"
    >
      {icon} {label}
    </button>
  );
}
