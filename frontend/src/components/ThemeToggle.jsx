import { Moon, Sun } from "lucide-react";
import { useTheme } from "../lib/theme";

export function ThemeToggle() {
    const { theme, toggle } = useTheme();
    const isDark = theme === "dark";
    return (
        <button
            type="button"
            onClick={toggle}
            aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
            data-testid="theme-toggle"
            className="inline-flex h-9 w-9 items-center justify-center rounded-md
                border border-border text-muted-foreground
                transition-colors duration-200
                hover:text-foreground hover:border-foreground/30
                focus:outline-none focus:ring-2 focus:ring-accent"
        >
            {isDark ? (
                <Sun className="h-4 w-4" strokeWidth={1.75} />
            ) : (
                <Moon className="h-4 w-4" strokeWidth={1.75} />
            )}
        </button>
    );
}
