import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { toast } from "sonner";
import {
    Clock,
    ExternalLink,
    ListChecks,
    Target,
    GitBranch,
    Compass,
    Info,
    Sparkles ,
} from "lucide-react";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { ExportShare } from "./ExportShare";
import { LanguageToggle } from "./LanguageToggle";
import { LatestUpdates } from "./LatestUpdates";
import { SentimentBias } from "./SentimentBias";
import { translateArticle } from "../lib/api";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";

function fmt(iso) {
    if (!iso) return null;
    try {
        return new Date(iso).toLocaleString();
    } catch {
        return iso;
    }
}

const stagger = {
    hidden: { opacity: 0, y: 12 },
    show: (i = 0) => ({
        opacity: 1,
        y: 0,
        transition: { duration: 0.45, ease: "easeOut", delay: i * 0.05 },
    }),
};

export function ResultCard({ summary, cached }) {
    const url = summary?.article?.original_url;
    // Cache summaries per language. `id` is always the initial payload from
    // the /summarize call; `en` is fetched on-demand via /translate.
    const [langCache, setLangCache] = useState({ id: summary, en: null });
    const [lang, setLang] = useState("id");
    const [loadingTarget, setLoadingTarget] = useState(null);

    // Reset local cache when a new summary flows in (different article).
    useEffect(() => {
        setLangCache({ id: summary, en: null });
        setLang("id");
        setLoadingTarget(null);
    }, [summary]);

    const handleLangChange = async (target) => {
        if (target === lang || loadingTarget) return;
        if (langCache[target]) {
            setLang(target);
            return;
        }
        setLoadingTarget(target);
        try {
            const res = await translateArticle(url, target);
            setLangCache((prev) => ({ ...prev, [target]: res.summary }));
            setLang(target);
        } catch (e) {
            const msg =
                e?.response?.data?.detail ||
                e?.message ||
                "Could not translate the summary.";
            toast.error(msg);
        } finally {
            setLoadingTarget(null);
        }
    };

    const displayed = langCache[lang] || summary;
    const a = displayed.article || {};
    const w = displayed.five_w_one_h || {};
    const wKeys = ["who", "what", "when", "where", "why", "how"];

    return (
        <section
            id="result"
            className="mx-auto max-w-5xl px-6 lg:px-10 py-16 scroll-mt-24"
            data-testid="result-section"
        >
            <motion.div
                initial="hidden"
                animate="show"
                variants={stagger}
                className="surface-card p-6 md:p-10 relative"
            >
                {/* Confidence + cached indicator */}
                <div className="absolute top-4 right-4 flex items-center gap-2">
                    {cached && (
                        <span className="text-[10px] uppercase tracking-widest
                            text-muted-foreground font-mono-alt border
                            border-border rounded-full px-2 py-0.5">
                            Cached
                        </span>
                    )}
                    <ConfidenceBadge
                        level={displayed.confidence_level?.level}
                        reason={displayed.confidence_level?.reason}
                    />
                </div>

                {/* Article info */}
                <motion.div variants={stagger} custom={0}>
                    <div className="label-eyebrow flex items-center gap-2">
                        <span>Article</span>
                        <span className="text-border">/</span>
                        <span>{a.category || "General"}</span>
                    </div>
                    <h2
                        className="mt-3 font-display text-2xl sm:text-3xl lg:text-4xl
                            font-medium tracking-tight leading-tight max-w-3xl"
                        data-testid="article-title"
                    >
                        {a.title || "Untitled"}
                    </h2>
                    <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1
                        text-xs font-mono-alt text-muted-foreground">
                        {a.site_name && <span>{a.site_name}</span>}
                        {a.publication_date && (
                            <>
                                <span>·</span>
                                <span>Published {a.publication_date}</span>
                            </>
                        )}
                        <span>·</span>
                        <span className="inline-flex items-center gap-1">
                            <Clock className="h-3 w-3" /> ~{a.reading_time_minutes || 3} min
                        </span>
                        <span>·</span>
                        <span>Generated {fmt(a.generated_at)}</span>
                        {a.original_url && (
                            <>
                                <span>·</span>
                                <a
                                    href={a.original_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="inline-flex items-center gap-1 text-accent hover:underline"
                                    data-testid="article-original-link"
                                >
                                    Original <ExternalLink className="h-3 w-3" />
                                </a>
                            </>
                        )}
                    </div>
                </motion.div>

                {/* Export/Share + language toggle */}
                <motion.div
                    variants={stagger}
                    custom={1}
                    className="mt-6 flex flex-wrap items-center justify-between gap-3"
                >
                    <ExportShare summary={displayed} />
                    <LanguageToggle
                        current={lang}
                        onChange={handleLangChange}
                        loading={!!loadingTarget}
                        target={loadingTarget}
                    />
                </motion.div>

                <hr className="my-8 border-border" />

                {/* Executive Summary */}
                <motion.div variants={stagger} custom={2}>
                    <div className="label-eyebrow">1 · Executive Summary</div>
                    <p
                        className="mt-3 text-lg md:text-xl leading-relaxed
                            text-foreground/90 font-display font-normal max-w-4xl"
                        data-testid="executive-summary"
                    >
                        {displayed.executive_summary}
                    </p>
                </motion.div>

                {/* Grid: Key Points + Main Issue + Root Cause */}
                <div className="mt-10 grid gap-4 md:grid-cols-6">
                    <motion.div
                        variants={stagger}
                        custom={3}
                        className="cell md:col-span-3"
                        data-testid="key-points"
                    >
                        <div className="cell-heading flex items-center gap-2">
                            <ListChecks className="h-3.5 w-3.5" strokeWidth={1.75} />
                            2 · Key Points
                        </div>
                        <ol className="space-y-2">
                            {(displayed.key_points || []).map((k, i) => (
                                <li key={i} className="flex gap-3 text-sm leading-relaxed">
                                    <span className="font-mono-alt text-muted-foreground mt-0.5">
                                        0{i + 1}
                                    </span>
                                    <span>{k}</span>
                                </li>
                            ))}
                        </ol>
                    </motion.div>

                    <motion.div
                        variants={stagger}
                        custom={4}
                        className="cell md:col-span-3"
                        data-testid="main-issue"
                    >
                        <div className="cell-heading flex items-center gap-2">
                            <Target className="h-3.5 w-3.5" strokeWidth={1.75} />
                            3 · Main Issue
                        </div>
                        <p className="text-sm leading-relaxed">
                            {displayed.main_issue?.summary}
                        </p>
                        {displayed.main_issue?.significance && (
                            <p className="mt-3 text-xs text-muted-foreground border-l-2 border-accent pl-3">
                                {displayed.main_issue.significance}
                            </p>
                        )}
                    </motion.div>

                    <motion.div
                        variants={stagger}
                        custom={5}
                        className="cell md:col-span-6"
                        data-testid="root-cause"
                    >
                        <div className="cell-heading flex items-center gap-2">
                            <GitBranch className="h-3.5 w-3.5" strokeWidth={1.75} />
                            4 · Root Cause
                        </div>
                        <ul className="space-y-2">
                            {(displayed.root_cause?.causes || []).map((c, i) => (
                                <li key={i} className="flex gap-3 text-sm leading-relaxed">
                                    <span className="font-mono-alt text-muted-foreground mt-0.5">
                                        →
                                    </span>
                                    <span>{c}</span>
                                </li>
                            ))}
                        </ul>
                        {displayed.root_cause?.certainty_note && (
                            <p className="mt-3 text-xs text-muted-foreground italic">
                                {displayed.root_cause.certainty_note}
                            </p>
                        )}
                    </motion.div>
                </div>

                {/* Sentiment & Bias Analysis (5) */}
                <motion.div
                    variants={stagger}
                    custom={5}
                    className="mt-10"
                >
                    <SentimentBias data={displayed.sentiment_and_bias} />
                </motion.div>

                {/* Recommended Actions */}
                <motion.div
                    variants={stagger}
                    custom={6}
                    className="mt-10"
                    data-testid="recommended-actions"
                >
                    <div className="flex items-center justify-between flex-wrap gap-2">
                        <div className="label-eyebrow flex items-center gap-2">
                            <Compass className="h-3.5 w-3.5" strokeWidth={1.75} />
                            6 · Recommended Actions
                        </div>
                        <Popover>
                            <PopoverTrigger asChild>
                                <button
                                    type="button"
                                    className="text-muted-foreground hover:text-foreground transition-colors"
                                    aria-label="AI attribution"
                                >
                                    <Sparkles className="h-3.5 w-3.5" strokeWidth={1.75} />
                                </button>
                            </PopoverTrigger>
                            <PopoverContent className="w-64 text-xs" side="top">
                                Generated by Claude (Anthropic) based on this article's content.
                            </PopoverContent>
                        </Popover>
                    </div>
                    <div className="mt-4 grid gap-4 md:grid-cols-3">
                        {[
                            ["Immediate", displayed.recommended_actions?.immediate],
                            ["Short-term", displayed.recommended_actions?.short_term],
                            ["Long-term", displayed.recommended_actions?.long_term],
                        ].map(([label, items]) => (
                            <div key={label} className="cell">
                                <div className="text-xs font-semibold tracking-wide text-foreground mb-3">
                                    {label}
                                </div>
                                <ol className="space-y-2">
                                    {(items || []).map((it, i) => (
                                        <li
                                            key={i}
                                            className="flex gap-2 text-sm leading-relaxed"
                                        >
                                            <span className="font-mono-alt text-muted-foreground">
                                                0{i + 1}
                                            </span>
                                            <span>{it}</span>
                                        </li>
                                    ))}
                                </ol>
                            </div>
                        ))}
                    </div>
                    {displayed.recommended_actions?.disclaimer && (
                        <p className="mt-3 text-xs text-muted-foreground flex items-center gap-1">
                            <Info className="h-3 w-3" strokeWidth={1.75} />
                            {displayed.recommended_actions.disclaimer}
                        </p>
                    )}
                </motion.div>

                {/* Latest Updates */}
                <motion.div variants={stagger} custom={7} className="mt-10">
                    <LatestUpdates
                        topic={a.title || displayed.main_issue?.summary || ""}
                        sourceUrl={a.original_url}
                    />
                </motion.div>

                {/* 5W1H */}
                <motion.div
                    variants={stagger}
                    custom={8}
                    className="mt-10"
                    data-testid="five-w-one-h"
                >
                    <div className="label-eyebrow">8 · 5W1H</div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {wKeys.map((k) => (
                            <div key={k} className="cell">
                                <div className="text-[10px] font-mono-alt uppercase tracking-widest text-muted-foreground">
                                    {k}
                                </div>
                                <p className="mt-2 text-sm leading-relaxed">
                                    {w[k] || "—"}
                                </p>
                            </div>
                        ))}
                    </div>
                </motion.div>

                {/* References */}
                <motion.div
                    variants={stagger}
                    custom={9}
                    className="mt-10"
                    data-testid="references"
                >
                    <div className="label-eyebrow">9 · References</div>
                    <ul className="mt-4 divide-y divide-border border border-border rounded-md">
                        {(displayed.references || []).map((r, i) => (
                            <li key={i} className="p-4 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4">
                                <span className="font-mono-alt text-xs text-muted-foreground w-10">
                                    [{String(i + 1).padStart(2, "0")}]
                                </span>
                                <div className="flex-1 min-w-0">
                                    <div className="text-sm font-medium truncate">
                                        {r.title || r.url}
                                    </div>
                                    <div className="text-xs text-muted-foreground font-mono-alt truncate">
                                        {r.website}
                                        {r.date ? ` · ${r.date}` : ""}
                                    </div>
                                </div>
                                <a
                                    href={r.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-xs inline-flex items-center gap-1 text-accent hover:underline shrink-0"
                                >
                                    Open <ExternalLink className="h-3 w-3" />
                                </a>
                            </li>
                        ))}
                    </ul>
                </motion.div>

                {/* Confidence explanation footer */}
                <motion.div
                    variants={stagger}
                    custom={10}
                    className="mt-10 border-t border-border pt-6 flex items-start gap-3"
                >
                    <div className="label-eyebrow shrink-0 pt-1">10 · Confidence</div>
                    <p className="text-sm text-muted-foreground leading-relaxed">
                        <span className="text-foreground font-medium">
                            {displayed.confidence_level?.level}
                        </span>
                        {displayed.confidence_level?.reason
                            ? ` — ${displayed.confidence_level.reason}`
                            : ""}
                    </p>
                </motion.div>
            </motion.div>
        </section>
    );
}
