import { Github, Instagram, Linkedin, Newspaper } from "lucide-react";

export function Footer() {
    const scrollTo = (id) => {
        const el = document.getElementById(id);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    const socials = [
        {
            icon: Github,
            href: "https://github.com/piqurs",
            label: "GitHub",
        },
        {
            icon: Linkedin,
            href: "https://www.linkedin.com/in/syafiqur-rahman-3b2891180/",
            label: "LinkedIn",
        },
        {
            icon: Instagram,
            href: "https://www.instagram.com/sipiquraman/",
            label: "Instagram",
        },
    ];

    return (
        <footer
            className="border-t border-border mt-auto"
            data-testid="site-footer"
        >
            <div className="mx-auto max-w-6xl px-6 lg:px-10 py-10 flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
                <button
                    type="button"
                    onClick={() => scrollTo("top")}
                    className="flex items-center gap-2 group"
                    data-testid="footer-logo"
                >
                    <span className="inline-flex h-7 w-7 items-center justify-center
                        rounded-md border border-border bg-card">
                        <Newspaper className="h-3.5 w-3.5" strokeWidth={1.75} />
                    </span>
                    <span className="font-display font-medium text-sm">
                        News Summarizer
                    </span>
                </button>

                <nav className="flex items-center gap-4 text-sm">
                    <button
                        type="button"
                        onClick={() => scrollTo("top")}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                        data-testid="footer-home"
                    >
                        Home
                    </button>
                    <button
                        type="button"
                        onClick={() => scrollTo("about")}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                        data-testid="footer-about"
                    >
                        About
                    </button>
                    <span className="text-border">·</span>
                    {socials.map((s) => (
                        <a
                            key={s.label}
                            href={s.href}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={s.label}
                            className="text-muted-foreground hover:text-foreground transition-colors"
                            data-testid={`footer-${s.label.toLowerCase()}`}
                        >
                            <s.icon className="h-4 w-4" strokeWidth={1.75} />
                        </a>
                    ))}
                </nav>

                <p className="text-xs text-muted-foreground font-mono-alt">
                    © 2026 News Summarizer
                </p>
            </div>
        </footer>
    );
}
