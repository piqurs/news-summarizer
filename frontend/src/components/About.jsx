import { motion } from "framer-motion";
import {
    BookOpen,
    ListChecks,
    Target,
    GitBranch,
    Compass,
    Radio,
    Layers,
    FileDown,
    FileText,
    Smartphone,
    SunMoon,
    Scale,
    Languages,
} from "lucide-react";

const FEATURES = [
    {
        icon: BookOpen,
        title: "Executive Summary",
        blurb: "100–150 words distilling what happened and why it matters.",
        span: "md:col-span-4 md:row-span-2",
    },
    {
        icon: ListChecks,
        title: "Key Points",
        blurb: "Exactly five sharp bullets — no filler.",
        span: "md:col-span-4",
    },
    {
        icon: Target,
        title: "Main Issue",
        blurb: "The central problem and its significance.",
        span: "md:col-span-4",
    },
    {
        icon: GitBranch,
        title: "Root Cause",
        blurb: "Underlying drivers, with honest caveats when facts are thin.",
        span: "md:col-span-6",
    },
    {
        icon: Scale,
        title: "Sentiment & Bias Analysis",
        blurb: "Tone read + observable bias indicators grounded in the article itself — never editorial speculation.",
        span: "md:col-span-6",
    },
    {
        icon: Compass,
        title: "Recommended Actions",
        blurb: "Immediate, short-term & long-term — clearly labelled AI-generated.",
        span: "md:col-span-6",
    },
    {
        icon: Languages,
        title: "Bilingual (EN / ID)",
        blurb: "Indonesian by default. One-click English translation, cached so the toggle is free.",
        span: "md:col-span-6",
    },
    {
        icon: Radio,
        title: "Latest Updates",
        blurb: "On-demand live search across trusted publishers.",
        span: "md:col-span-4",
    },
    {
        icon: Layers,
        title: "5W1H",
        blurb: "Structured Who / What / When / Where / Why / How.",
        span: "md:col-span-4",
    },
    {
        icon: FileDown,
        title: "Export PDF",
        blurb: "Portable, print-ready analysis.",
        span: "md:col-span-2",
    },
    {
        icon: FileText,
        title: "Export Word",
        blurb: "Fully editable .docx.",
        span: "md:col-span-2",
    },
    {
        icon: Smartphone,
        title: "Responsive",
        blurb: "Reads beautifully from mobile to desktop.",
        span: "md:col-span-4",
    },
    {
        icon: SunMoon,
        title: "Light & Dark",
        blurb: "Two modes, both engineered to look correct.",
        span: "md:col-span-4",
    },
];

export function About() {
    return (
        <section
            id="about"
            className="border-t border-border scroll-mt-16"
            data-testid="about-section"
        >
            <div className="mx-auto max-w-6xl px-6 lg:px-10 py-24 md:py-32">
                <div className="grid gap-10 md:grid-cols-12">
                    <div className="md:col-span-5">
                        <div className="label-eyebrow">About</div>
                        <h2 className="mt-3 font-display text-3xl sm:text-4xl lg:text-5xl font-medium tracking-tight">
                            About News Summarizer
                        </h2>
                        <p className="mt-4 text-muted-foreground leading-relaxed">
                            AI-powered News Summarizer with root cause & multi-source
                            update analysis.
                        </p>
                        <p className="mt-6 text-sm text-muted-foreground leading-relaxed">
                            This goes beyond traditional summarization by identifying
                            the main issue, analyzing root causes, generating structured
                            insights, recommending possible actions, and — on demand —
                            enriching the analysis with a live search across trusted
                            publishers.
                        </p>
                    </div>

                    <div className="md:col-span-7">
                        <div className="grid grid-cols-1 md:grid-cols-12 gap-3">
                            {FEATURES.map((f, i) => (
                                <motion.div
                                    key={f.title}
                                    initial={{ opacity: 0, y: 10 }}
                                    whileInView={{ opacity: 1, y: 0 }}
                                    viewport={{ once: true, margin: "-40px" }}
                                    transition={{ duration: 0.4, delay: i * 0.03 }}
                                    className={`cell hover:border-foreground/30 hover:-translate-y-[2px]
                                        transition-[transform,border-color] duration-300
                                        ${f.span}`}
                                    data-testid={`feature-${f.title.toLowerCase().replace(/\s+/g, "-")}`}
                                >
                                    <div className="inline-flex h-8 w-8 items-center justify-center
                                        rounded-md border border-border bg-card mb-3">
                                        <f.icon className="h-4 w-4" strokeWidth={1.75} />
                                    </div>
                                    <div className="font-display font-medium">{f.title}</div>
                                    <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
                                        {f.blurb}
                                    </p>
                                </motion.div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        </section>
    );
}
