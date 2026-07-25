import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ExternalLink, Radio, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { getLatestUpdates } from "../lib/api";

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

export function LatestUpdates({ topic, sourceUrl }) {
    const [state, setState] = useState({ status: "idle", updates: [] });

    const fetchNow = async () => {
        setState({ status: "loading", updates: [] });
        try {
            const { updates } = await getLatestUpdates(topic, sourceUrl);
            setState({ status: "ready", updates });
            if (!updates?.length)
                toast.info(
                    "No newer public updates were found at the time of analysis.",
                );
        } catch (e) {
            const msg = e?.response?.data?.detail || "Live search failed.";
            setState({ status: "error", updates: [], error: msg });
            toast.error(msg);
        }
    };

    return (
        <div className="surface-card p-6" data-testid="latest-updates-section">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <span className="inline-flex h-8 w-8 items-center justify-center
                        rounded-md border border-border bg-card">
                        <Radio className="h-4 w-4" strokeWidth={1.75} />
                    </span>
                    <div>
                        <div className="label-eyebrow">7 · Latest Updates</div>
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

                {state.status === "ready" && state.updates.length > 0 && (
                    <motion.ul
                        key="list"
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        className="mt-5 space-y-3"
                    >
                        {state.updates.map((u, i) => (
                            <li
                                key={u.url + i}
                                className="border border-border rounded-md p-4
                                    hover:border-foreground/30 transition-colors"
                                data-testid={`update-item-${i}`}
                            >
                                <div className="flex items-center justify-between gap-3 flex-wrap">
                                    <div className="flex items-center gap-2 text-xs font-mono-alt text-muted-foreground">
                                        <span>{fmtDate(u.date)}</span>
                                        <span>·</span>
                                        <span className="uppercase tracking-wider">
                                            {u.source}
                                        </span>
                                    </div>
                                    <a
                                        href={u.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-xs inline-flex items-center gap-1 text-accent hover:underline"
                                    >
                                        Read <ExternalLink className="h-3 w-3" />
                                    </a>
                                </div>
                                <h4 className="mt-2 font-display font-medium leading-snug">
                                    {u.title}
                                </h4>
                                {u.summary && (
                                    <p className="mt-1 text-sm text-muted-foreground line-clamp-3">
                                        {u.summary}
                                    </p>
                                )}
                            </li>
                        ))}
                    </motion.ul>
                )}

                {state.status === "ready" && state.updates.length === 0 && (
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
