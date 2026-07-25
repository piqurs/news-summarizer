import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Navbar } from "../components/Navbar";
import { Hero } from "../components/Hero";
import { LoadingState } from "../components/LoadingState";
import { ResultCard } from "../components/ResultCard";
import { About } from "../components/About";
import { Footer } from "../components/Footer";
import { summarizeUrl } from "../lib/api";

export default function Home() {
    const [state, setState] = useState({
        status: "idle", // idle | loading | ready | error
        summary: null,
        cached: false,
        error: null,
    });
    const resultRef = useRef(null);
    const inFlight = useRef(false);

    const handleSubmit = async (url) => {
        if (inFlight.current) return;
        inFlight.current = true;
        setState({ status: "loading", summary: null, cached: false, error: null });

        try {
            const res = await summarizeUrl(url);
            setState({
                status: "ready",
                summary: res.summary,
                cached: !!res.cached,
                error: null,
            });
            if (res.cached) toast.success("Loaded from cache");
            else toast.success("Summary ready");
        } catch (e) {
            const msg =
                e?.response?.data?.detail ||
                e?.message ||
                "Something went wrong. Please try again.";
            setState({ status: "error", summary: null, cached: false, error: msg });
            toast.error(msg);
        } finally {
            inFlight.current = false;
        }
    };

    useEffect(() => {
        if (state.status === "ready" || state.status === "loading") {
            // Smooth scroll to result region shortly after render
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
                />

                <div id="result-anchor" ref={resultRef} />

                {state.status === "loading" && <LoadingState />}
                {state.status === "ready" && state.summary && (
                    <ResultCard summary={state.summary} cached={state.cached} />
                )}
                {state.status === "error" && (
                    <section
                        className="mx-auto max-w-3xl px-6 lg:px-10 py-16"
                        data-testid="error-state"
                    >
                        <div className="surface-card p-8 border-destructive/40">
                            <div className="label-eyebrow text-destructive">Error</div>
                            <p className="mt-3 font-display text-xl font-medium">
                                We couldn’t summarize that link.
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
