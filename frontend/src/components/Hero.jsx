import { useState } from "react";
import { ArrowRight, Link2, Sparkles } from "lucide-react";
import { motion } from "framer-motion";

export function Hero({ onSubmit, isLoading, defaultUrl = "" }) {
    const [url, setUrl] = useState(defaultUrl);

    const handle = (e) => {
        e.preventDefault();
        if (isLoading) return;
        let trimmed = url.trim();
        if (!trimmed) return;
        // Auto-add scheme so users can paste "example.com/article" without a
        // browser-level url-validation error.
        if (!/^https?:\/\//i.test(trimmed)) {
            trimmed = "https://" + trimmed;
            setUrl(trimmed);
        }
        onSubmit(trimmed);
    };

    const scrollToAbout = () => {
        document
            .getElementById("about")
            ?.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    return (
        <section
            id="top"
            className="relative overflow-hidden border-b border-border"
        >
            {/* Subtle radial glow at top */}
            <div
                aria-hidden
                className="pointer-events-none absolute inset-x-0 -top-40 h-80
                    bg-[radial-gradient(ellipse_60%_50%_at_50%_100%,hsl(var(--accent)/0.18),transparent_70%)]"
            />
            {/* Grid background */}
            <div
                aria-hidden
                className="pointer-events-none absolute inset-0 grid-bg opacity-40 [mask-image:radial-gradient(ellipse_at_center,black_30%,transparent_75%)]"
            />

            <div className="relative mx-auto max-w-5xl px-6 lg:px-10 pt-20 pb-24 md:pt-28 md:pb-32">
                <motion.div
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.5, ease: "easeOut" }}
                    className="flex items-center gap-2 mb-6"
                >
                    <span className="inline-flex items-center gap-2 rounded-full
                        border border-border bg-card/60 px-3 py-1 text-xs
                        text-muted-foreground font-mono-alt">
                        <Sparkles className="h-3 w-3" strokeWidth={2} />
                        Powered by Claude Sonnet 4.5
                    </span>
                </motion.div>

                <motion.h1
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.55, delay: 0.05, ease: "easeOut" }}
                    className="font-display text-4xl sm:text-5xl lg:text-6xl
                        font-medium tracking-tight leading-[1.05]"
                    data-testid="hero-headline"
                >
                    Summarize any news article
                    <br />
                    <span className="text-muted-foreground">in seconds.</span>
                </motion.h1>

                <motion.p
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.55, delay: 0.12, ease: "easeOut" }}
                    className="mt-6 max-w-2xl text-base md:text-lg text-muted-foreground
                        leading-relaxed"
                >
                    Paste a news article URL and instantly receive an AI-powered,
                    structured summary with key insights, root-cause analysis, and
                    the latest developments from trusted sources.
                </motion.p>

                <motion.form
                    initial={{ opacity: 0, y: 16 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.6, delay: 0.2, ease: "easeOut" }}
                    onSubmit={handle}
                    className="mt-10 flex flex-col sm:flex-row items-stretch gap-3
                        max-w-3xl"
                >
                    <label className="sr-only" htmlFor="article-url">
                        News article URL
                    </label>
                    <div
                        className="group flex-1 flex items-center gap-2
                            rounded-md border border-border bg-card
                            px-4 focus-within:border-accent focus-within:ring-2
                            focus-within:ring-accent/40 transition-colors"
                    >
                        <Link2
                            className="h-4 w-4 text-muted-foreground shrink-0"
                            strokeWidth={1.75}
                        />
                        <input
                            id="article-url"
                            type="text"
                            inputMode="url"
                            autoComplete="url"
                            spellCheck="false"
                            required
                            disabled={isLoading}
                            value={url}
                            onChange={(e) => setUrl(e.target.value)}
                            placeholder="https://reuters.com/world/…  (scheme optional)"
                            className="w-full bg-transparent py-4 text-base
                                placeholder:text-muted-foreground/60
                                focus:outline-none disabled:opacity-60"
                            data-testid="url-input"
                        />
                    </div>
                    <button
                        type="submit"
                        disabled={isLoading || !url.trim()}
                        className="btn-primary text-base px-6 py-4"
                        data-testid="summarize-button"
                    >
                        {isLoading ? "Summarizing…" : "Summarize"}
                        <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
                    </button>
                </motion.form>

                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.6, delay: 0.35 }}
                    className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2
                        text-xs text-muted-foreground font-mono-alt"
                >
                    <button
                        type="button"
                        onClick={scrollToAbout}
                        className="btn-ghost"
                        data-testid="learn-more-button"
                    >
                        Learn more →
                    </button>
                    <span className="hidden sm:inline">·</span>
                    <span>9 structured sections</span>
                    <span>·</span>
                    <span>PDF · DOCX · Share</span>
                    <span>·</span>
                    <span>Cached for 24 h</span>
                </motion.div>
            </div>
        </section>
    );
}
