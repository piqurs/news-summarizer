import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Clock, Newspaper, ArrowUpRight } from "lucide-react";
import { getRecent } from "../lib/api";

function fmtRelative(iso) {
    if (!iso) return "";
    try {
        const then = new Date(iso).getTime();
        const now = Date.now();
        const diff = Math.max(0, now - then);
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return "just now";
        if (mins < 60) return `${mins}m ago`;
        const hours = Math.floor(mins / 60);
        if (hours < 12) return `${hours}h ago`;
        const days = Math.floor(hours / 12);
        return `${days}d ago`;
    } catch {
        return "";
    }
}

/**
 * Global feed of the most recent still-fresh cached summaries. Purely reads
 * the existing 12 h cache — clicking a card triggers `onOpen(url)` which the
 * parent uses to load the cached summary into the main result card. No AI is
 * called and no rate limit is consumed by this component or its clicks.
 */
export function RecentNews({ refreshKey = 0, onOpen, loadingUrl }) {
    const [items, setItems] = useState(null); // null = loading, [] = empty

    useEffect(() => {
        let cancelled = false;
        setItems((prev) => (prev == null ? null : prev)); // keep prior on refresh
        getRecent(6)
            .then((r) => {
                if (!cancelled) setItems(r.items || []);
            })
            .catch(() => {
                if (!cancelled) setItems([]);
            });
        return () => {
            cancelled = true;
        };
    }, [refreshKey]);

    // Hide the whole section when the cache is empty — no need to advertise
    // a "recent" strip that has nothing in it.
    if (items != null && items.length === 0) return null;

    return (
        <section
            id="recent-news"
            className="border-t border-border"
            data-testid="recent-news-section"
        >
            <div className="mx-auto max-w-6xl px-6 lg:px-10 py-16 md:py-20">
                <div className="flex items-end justify-between flex-wrap gap-3 mb-8">
                    <div>
                        <div className="label-eyebrow flex items-center gap-2">
                            <Newspaper className="h-3.5 w-3.5" strokeWidth={1.75} />
                            Recent News · Last 12h
                        </div>
                        <h2
                            className="mt-2 font-display text-2xl sm:text-3xl
                                font-medium tracking-tight"
                            data-testid="recent-news-heading"
                        >
                            Recently generated summaries
                        </h2>
                        <p className="mt-2 text-sm text-muted-foreground max-w-xl">
                            A public feed of the latest summaries generated on this
                            app — click any card to open the full analysis instantly
                            from cache. No AI is re-run.
                        </p>
                    </div>
                </div>

                {items == null ? (
                    <div
                        className="grid gap-3 md:grid-cols-2 lg:grid-cols-3"
                        data-testid="recent-news-loading"
                    >
                        {[0, 1, 2, 3, 4, 5].map((i) => (
                            <div
                                key={i}
                                className="cell h-40 animate-pulse bg-secondary/60"
                            />
                        ))}
                    </div>
                ) : (
                    <AnimatePresence initial={false}>
                        <motion.div
                            key="grid"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className="grid gap-3 md:grid-cols-2 lg:grid-cols-3"
                        >
                            {items.map((it, i) => {
                                const busy = loadingUrl === it.url;
                                return (
                                    <motion.button
                                        type="button"
                                        key={it.hash || it.url || i}
                                        onClick={() => onOpen?.(it.url)}
                                        disabled={!!loadingUrl}
                                        initial={{ opacity: 0, y: 8 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        transition={{
                                            duration: 0.35,
                                            delay: i * 0.03,
                                            ease: "easeOut",
                                        }}
                                        className="cell text-left group relative
                                            hover:border-foreground/30
                                            hover:-translate-y-[2px]
                                            transition-[transform,border-color]
                                            duration-300
                                            disabled:opacity-60 disabled:cursor-wait"
                                        data-testid={`recent-news-card-${i}`}
                                    >
                                        <div className="flex items-start justify-between gap-3">
                                            <span
                                                className="text-[10px] font-mono-alt
                                                    uppercase tracking-widest
                                                    text-muted-foreground border
                                                    border-border rounded-full
                                                    px-2 py-0.5"
                                            >
                                                {it.category || "General"}
                                            </span>
                                            <ArrowUpRight
                                                className="h-4 w-4 text-muted-foreground
                                                    group-hover:text-foreground
                                                    transition-colors shrink-0"
                                                strokeWidth={1.75}
                                            />
                                        </div>
                                        <h3
                                            className="mt-3 font-display font-medium
                                                leading-snug line-clamp-3"
                                            data-testid={`recent-news-title-${i}`}
                                        >
                                            {it.title}
                                        </h3>
                                        <div
                                            className="mt-4 flex items-center gap-2
                                                text-xs font-mono-alt
                                                text-muted-foreground"
                                        >
                                            <span className="truncate max-w-[10rem]">
                                                {it.site_name || "source"}
                                            </span>
                                            <span>·</span>
                                            <span className="inline-flex items-center gap-1">
                                                <Clock
                                                    className="h-3 w-3"
                                                    strokeWidth={1.75}
                                                />
                                                {fmtRelative(it.generated_at)}
                                            </span>
                                            {busy && (
                                                <>
                                                    <span>·</span>
                                                    <span className="text-accent">
                                                        loading…
                                                    </span>
                                                </>
                                            )}
                                        </div>
                                    </motion.button>
                                );
                            })}
                        </motion.div>
                    </AnimatePresence>
                )}
            </div>
        </section>
    );
}
