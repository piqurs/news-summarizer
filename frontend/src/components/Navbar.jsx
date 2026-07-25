import { Newspaper } from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";

export function Navbar() {
    const scrollTo = (id) => {
        const el = document.getElementById(id);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    return (
        <header
            className="sticky top-0 z-50 w-full backdrop-blur-xl
                bg-background/70 border-b border-border"
            data-testid="site-navbar"
        >
            <div className="mx-auto max-w-6xl px-6 lg:px-10 h-16 flex items-center justify-between">
                <button
                    type="button"
                    onClick={() => scrollTo("top")}
                    className="flex items-center gap-2 group"
                    data-testid="nav-logo"
                    aria-label="News Summarizer home"
                >
                    <span className="inline-flex h-8 w-8 items-center justify-center
                        rounded-md border border-border bg-card
                        transition-colors group-hover:border-foreground/40">
                        <Newspaper className="h-4 w-4" strokeWidth={1.75} />
                    </span>
                    <span className="font-display text-base font-medium tracking-tight">
                        News Summarizer
                    </span>
                </button>

                <nav className="flex items-center gap-1 sm:gap-2">
                    <button
                        type="button"
                        onClick={() => scrollTo("top")}
                        className="btn-ghost"
                        data-testid="nav-home"
                    >
                        Home
                    </button>
                    <button
                        type="button"
                        onClick={() => scrollTo("about")}
                        className="btn-ghost"
                        data-testid="nav-about"
                    >
                        About
                    </button>
                    <ThemeToggle />
                </nav>
            </div>
        </header>
    );
}
