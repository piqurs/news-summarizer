import { useEffect, useState } from "react";
import { motion } from "framer-motion";

const MESSAGES = [
    "Fetching article…",
    "Extracting main content…",
    "Analyzing structure & entities…",
    "Identifying the main issue…",
    "Reasoning about root causes…",
    "Drafting recommended actions…",
    "Assembling the structured summary…",
];

export function LoadingState() {
    const [idx, setIdx] = useState(0);
    useEffect(() => {
        const id = setInterval(
            () => setIdx((i) => (i + 1) % MESSAGES.length),
            2200,
        );
        return () => clearInterval(id);
    }, []);

    return (
        <section
            className="mx-auto max-w-5xl px-6 lg:px-10 py-16"
            data-testid="loading-state"
        >
            <div className="surface-card p-8 md:p-10 relative overflow-hidden">
                <div className="label-eyebrow">Working</div>

                <div className="mt-3 flex items-center gap-3">
                    <div className="relative h-3 w-3">
                        <span className="absolute inset-0 rounded-full bg-accent" />
                        <span className="absolute inset-0 rounded-full bg-accent animate-ping opacity-60" />
                    </div>
                    <motion.p
                        key={idx}
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -6 }}
                        transition={{ duration: 0.35 }}
                        className="font-display text-xl md:text-2xl font-medium tracking-tight"
                        data-testid="loading-message"
                    >
                        {MESSAGES[idx]}
                    </motion.p>
                </div>

                <div className="mt-6 h-1 w-full overflow-hidden rounded-full bg-secondary">
                    <motion.div
                        className="h-full bg-accent"
                        initial={{ x: "-100%" }}
                        animate={{ x: "100%" }}
                        transition={{
                            duration: 1.6,
                            repeat: Infinity,
                            ease: "easeInOut",
                        }}
                        style={{ width: "40%" }}
                    />
                </div>

                {/* Skeleton block preview */}
                <div className="mt-8 grid gap-4 md:grid-cols-6">
                    <div className="md:col-span-6 h-6 rounded bg-secondary animate-pulse" />
                    <div className="md:col-span-4 h-4 rounded bg-secondary animate-pulse" />
                    <div className="md:col-span-2 h-4 rounded bg-secondary animate-pulse" />
                    <div className="md:col-span-3 h-24 rounded bg-secondary animate-pulse" />
                    <div className="md:col-span-3 h-24 rounded bg-secondary animate-pulse" />
                </div>
            </div>
        </section>
    );
}
