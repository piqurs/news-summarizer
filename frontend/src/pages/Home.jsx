import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Clock4 } from "lucide-react";
import { Navbar } from "../components/Navbar";
import { Hero } from "../components/Hero";
import { LoadingState } from "../components/LoadingState";
import { ResultCard } from "../components/ResultCard";
import { About } from "../components/About";
import { Footer } from "../components/Footer";
import { getRateStatus, summarizeUrl } from "../lib/api";

export default function Home() {
    const [state, setState] = useState({
        status: "idle", // idle | loading | ready | error | rate-limited
        summary: null,
        cached: false,
        error: null,
        retryAfterSeconds: 0,
    });
    const [rateStatus, setRateStatus] = useState(null); // { limit, remaining }
    const inFlight = useRef(false);

    // Initial rate status on mount
    useEffect(() => {
        getRateStatus()
            .then((r) => setRateStatus(r.summarize))
            .catch(() => setRateStatus(null));
    }, []);

    const handleSubmit = async (url) => {
        if (inFlight.current) return;
        inFlight.current = true;
        setState({
            status: "loading",
            summary: null,
            cached: false,
            error: null,
            retryAfterSeconds: 0,
        });

        try {
            const res = await summarizeUrl(url);
            setState({
                status: "ready",
                summary: res.summary,
                cached: !!res.cached,
                error: null,
                retryAfterSeconds: 0,
            });
            if (res.rate_limit) setRateStatus(res.rate_limit);
            if (res.cached) toast.success("Loaded from cache");
            else toast.success("Summary ready");
        } catch (e) {
            const data = e?.response?.data;
            const status = e?.response?.status;
            const msg =
                data?.detail || e?.message || "Something went wrong. Please try again.";
            if (status === 429 && data?.rate_limit) {
                setRateStatus({
                    limit: data.rate_limit.limit,
                    remaining: 0,
                });
                setState({
                    status: "rate-limited",
                    summary: null,
                    cached: false,
                    error: msg,
                    retryAfterSeconds: data.rate_limit.retry_after_seconds || 0,
                });
                toast.error(msg);
            } else {
                setState({
                    status: "error",
                    summary: null,
                    cached: false,
                    error: msg,
                    retryAfterSeconds: 0,
                });
                toast.error(msg);
            }
        } finally {
            inFlight.current = false;
        }
    };

    useEffect(() => {
        if (state.status !== "idle") {
            const t = setTimeout(() => {
                document
                    .getElementById("result-anchor")
                    ?.scrollIntoView({ behavior: "smooth", block: "start" });
            }, 120);
            return () => clearTimeout(t);
        }
    }, [state.status]);

    return (
        <div className="App min-h-screen flex flex-col">
            <Navbar />
            <main className="flex-1">
                <Hero
                    onSubmit={handleSubmit}
                    isLoading={state.status === "loading"}
                    rateStatus={rateStatus}
                />

                <div id="result-anchor" />

                {state.status === "loading" && <LoadingState />}

                {state.status === "ready" && state.summary && (
                    <ResultCard summary={state.summary} cached={state.cached} />
                )}

                {state.status === "rate-limited" && (
                    <section
                        className="mx-auto max-w-3xl px-6 lg:px-10 py-16"
                        data-testid="rate-limit-state"
                    >
                        <div className="surface-card p-8 border-amber-500/40">
                            <div className="flex items-start gap-4">
                                <span className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-amber-500/40 bg-amber-500/10">
                                    <Clock4
                                        className="h-5 w-5 text-amber-500"
                                        strokeWidth={1.75}
                                    />
                                </span>
                                <div>
                                    <div className="label-eyebrow text-amber-500">
                                        Hourly limit reached
                                    </div>
                                    <p className="mt-2 font-display text-xl font-medium">
                                        You&apos;ve used all {rateStatus?.limit ?? 5} free
                                        summaries for this hour.
                                    </p>
                                    <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
                                        {state.error}
                                    </p>
                                    <p className="mt-4 text-xs text-muted-foreground">
                                        Tip · re-submitting an article you already
                                        summarized this session loads instantly from
                                        cache and does <span className="text-foreground font-medium">not</span> count against
                                        the limit.
                                    </p>
                                </div>
                            </div>
                        </div>
                    </section>
                )}

                {state.status === "error" && (
                    <section
                        className="mx-auto max-w-3xl px-6 lg:px-10 py-16"
                        data-testid="error-state"
                    >
                        <div className="surface-card p-8 border-destructive/40">
                            <div className="label-eyebrow text-destructive">Error</div>
                            <p className="mt-3 font-display text-xl font-medium">
                                We couldn&apos;t summarize that link.
                            </p>
                            <p className="mt-2 text-sm text-muted-foreground">
                                {state.error}
                            </p>
                            <p className="mt-4 text-xs text-muted-foreground">
                                Try a different article, or check whether the site
                                blocks automated readers.
                            </p>
                        </div>
                    </section>
                )}

                <About />
            </main>
            <Footer />
        </div>
    );
}
