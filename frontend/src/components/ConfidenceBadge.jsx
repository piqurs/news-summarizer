export function ConfidenceBadge({ level, reason }) {
    const l = (level || "").toLowerCase();
    const map = {
        high: { dot: "bg-emerald-500", label: "High confidence" },
        medium: { dot: "bg-amber-500", label: "Medium confidence" },
        low: { dot: "bg-rose-500", label: "Low confidence" },
    };
    const cfg = map[l] || {
        dot: "bg-muted-foreground",
        label: "Confidence unknown",
    };
    return (
        <div
            className="inline-flex items-center gap-2 rounded-full border border-border
                bg-card/70 px-3 py-1"
            title={reason || ""}
            data-testid="confidence-badge"
        >
            <span className={`h-1.5 w-1.5 rounded-full ${cfg.dot}`} />
            <span className="text-xs font-medium">{cfg.label}</span>
        </div>
    );
}
