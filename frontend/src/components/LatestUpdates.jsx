import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ExternalLink, Radio, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { getLatestUpdates } from "../lib/api";
import { ConfidenceBadge } from "./ConfidenceBadge";

function fmtDate(d) {
    if (!d) return "Unknown date";
    try {
        return new Date(d).toLocaleDateString(undefined, {
            year: "numeric",
            month: "short",
            day: "numeric",
        });
    } catch {
        return d;
    }
}

export function LatestUpdates({ topic, sourceUrl, onDataChange }) {
    const [state, setState] = useState({ status: "idle", delta: null });

    const fetchNow = async () => {
        setState({ status: "loading", delta: null });
        onDataChange?.(null);
        try {
            const delta = await getLatestUpdates(topic, sourceUrl);
            setState({ status: "ready", delta });
            onDataChange?.(delta);
            if (!delta?.has_update)
                toast.info(
                    "No newer public updates were found at the time of analysis.",
                );
        } catch (e) {
            const msg = e?.response?.data?.detail || "Live search failed.";
            setState({ status: "error", delta: null, error: msg });
            onDataChange?.(null);
            toast.error(msg);
        }
    };

    const d = state.delta;

    return (
        <div className="surface-card p-6" data-testid="latest-updates-section">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <span className="inline-flex h-8 w-8 items-center justify-center
                        rounded-md border border-border bg-card">
                        <Radio className="h-4 w-4" strokeWidth={1.75} />
                    </span>
                    <div>
                        <div className="label-eyebrow">10 · Latest Updates</div>
                        <p className="text-sm text-muted-foreground mt-1">
                            Fetch newer public reporting on this topic from trusted
                            sources.
                        </p>
                    </div>
                </div>

                <button
                    onClick={fetchNow}
                    disabled={state.status === "loading"}
                    className="btn-secondary text-sm"
                    data-testid="show-latest-updates"
                >
                    {state.status === "loading" ? (
                        <>
                            <RefreshCw className="h-4 w-4 animate-spin" strokeWidth={1.75} />
                            Searching…
                        </>
                    ) : state.status === "ready" ? (
                        <>
                            <RefreshCw className="h-4 w-4" strokeWidth={1.75} />
                            Refresh
                        </>
                    ) : (
                        "Show Latest Updates"
                    )}
                </button>
            </div>

            <AnimatePresence initial={false}>
                {state.status === "loading" && (
                    <motion.div
                        key="loading"
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        className="mt-4 space-y-2"
                    >
                        {[0, 1, 2].map((i) => (
                            <div
                                key={i}
                                className="h-14 rounded-md bg-secondary animate-pulse"
                            />
                        ))}
                    </motion.div>
                )}

                {state.status === "ready" && d?.has_update && (
                    <motion.div
                        key="delta"
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        className="mt-5 space-y-5"
                    >
                        {d.overview && (
                            <p className="text-sm leading-relaxed">{d.overview}</p>
                        )}

                        {d.developments?.length > 0 && (
                            <div>
                                <div className="label-eyebrow mb-2">Perkembangan Terbaru</div>
                                <ul className="space-y-2">
                                    {d.developments.map((dev, i) => (
                                        <li key={i} className="flex gap-2 text-sm">
                                            <span className="text-muted-foreground">→</span>
                                            <span>{dev}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {d.timeline?.length > 0 && (
                            <div>
                                <div className="label-eyebrow mb-2">Timeline</div>
                                <ul className="space-y-2 border-l border-border pl-4">
                                    {d.timeline.map((t, i) => (
                                        <li key={i} className="text-sm">
                                            <span className="font-mono-alt text-xs text-muted-foreground">
                                                {fmtDate(t.date)}
                                            </span>
                                            <span className="block">{t.event}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {d.current_situation && (
                            <div>
                                <div className="label-eyebrow mb-2">Situasi Terkini</div>
                                <p className="text-sm text-muted-foreground">
                                    {d.current_situation}
                                </p>
                            </div>
                        )}

                        {d.market_impact && (
                            <div>
                                <div className="label-eyebrow mb-2">Dampak Pasar</div>
                                <p className="text-sm text-muted-foreground">
                                    {d.market_impact}
                                </p>
                            </div>
                        )}

                        <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-border">
                            <ConfidenceBadge
                                level={d.confidence?.level}
                                reason={d.confidence?.reason}
                            />
                            <div className="flex flex-wrap gap-3">
                                {d.sources_used?.map((url, i) => (
                                    <a
                                        key={url + i}
                                        href={url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-xs inline-flex items-center gap-1 text-accent hover:underline"
                                    >
                                        Sumber {i + 1} <ExternalLink className="h-3 w-3" />
                                    </a>
                                ))}
                            </div>
                        </div>
                    </motion.div>
                )}

                {state.status === "ready" && !d?.has_update && (
                    <motion.p
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="mt-5 text-sm text-muted-foreground"
                    >
                        No newer public updates were found at the time of analysis.
                    </motion.p>
                )}

                {state.status === "error" && (
                    <motion.p
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="mt-5 text-sm text-destructive"
                    >
                        {state.error}
                    </motion.p>
                )}
            </AnimatePresence>
        </div>
    );
}
