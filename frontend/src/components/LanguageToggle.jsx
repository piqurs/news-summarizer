import { Loader2 } from "lucide-react";

/**
 * Segmented ID | EN language toggle used in the summary result card.
 * The parent owns the state; this component is purely presentational.
 */
export function LanguageToggle({ current, onChange, loading, target }) {
    const langs = [
        { code: "id", label: "ID" },
        { code: "en", label: "EN" },
    ];
    return (
        <div
            className="inline-flex items-center gap-1 rounded-full border
                border-border bg-card/70 p-0.5"
            data-testid="language-toggle"
            role="group"
            aria-label="Summary language"
        >
            {langs.map((l) => {
                const active = current === l.code;
                const isBusy = loading && target === l.code;
                return (
                    <button
                        key={l.code}
                        type="button"
                        onClick={() => onChange(l.code)}
                        disabled={loading}
                        data-testid={`lang-btn-${l.code}`}
                        aria-pressed={active}
                        className={
                            "relative inline-flex items-center gap-1 rounded-full " +
                            "px-3 py-1 text-xs font-medium tracking-wide " +
                            "transition-colors focus:outline-none focus:ring-2 " +
                            "focus:ring-accent disabled:cursor-not-allowed " +
                            (active
                                ? "bg-foreground text-background"
                                : "text-muted-foreground hover:text-foreground")
                        }
                    >
                        {isBusy && (
                            <Loader2
                                className="h-3 w-3 animate-spin"
                                strokeWidth={2}
                            />
                        )}
                        {l.label}
                    </button>
                );
            })}
        </div>
    );
}
