import { jsPDF } from "jspdf";
import {
    Document,
    Packer,
    Paragraph,
    HeadingLevel,
    TextRun,
    AlignmentType,
    BorderStyle,
    LevelFormat,
} from "docx";
import { saveAs } from "file-saver";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const SHARE_HOST = "https://test-50.emergent.host";

function fmtDate(iso) {
    if (!iso) return "";
    try {
        return new Date(iso).toLocaleString();
    } catch {
        return iso;
    }
}

function fmtShortDate(d) {
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

/**
 * Build a URL-safe slug from the full article title. Removes diacritics,
 * lowercases, collapses non alphanumerics into single hyphens.
 * Never returns an empty string — falls back to `article` if title is empty.
 */
export function slugifyTitle(title) {
    const base = (title || "article")
        .toString()
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .replace(/&/g, " and ")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
    return base || "article";
}

export function buildShareUrl(summary) {
    const slug = slugifyTitle(summary?.article?.title);
    return `${SHARE_HOST}/${slug}`;
}

// ---------------------------------------------------------------------------
// Plain-text serialisation (used for clipboard + Web Share fallback)
// ---------------------------------------------------------------------------

function plainSummaryText(s, latestUpdates) {
    const lines = [];
    const a = s.article || {};
    lines.push(a.title || "Untitled");
    lines.push("");
    lines.push(`Category: ${a.category || ""}`);
    lines.push(`Source: ${a.original_url || ""}`);
    if (a.publication_date) lines.push(`Published: ${a.publication_date}`);
    lines.push(`Generated: ${fmtDate(a.generated_at)}`);
    lines.push(`Reading time: ~${a.reading_time_minutes} min`);
    lines.push("");
    lines.push("EXECUTIVE SUMMARY");
    lines.push(s.executive_summary || "");
    lines.push("");
    lines.push("KEY POINTS");
    (s.key_points || []).forEach((k, i) => lines.push(`${i + 1}. ${k}`));
    lines.push("");
    lines.push("MAIN ISSUE");
    lines.push(s.main_issue?.summary || "");
    if (s.main_issue?.significance) {
        lines.push("");
        lines.push(`Significance: ${s.main_issue.significance}`);
    }
    lines.push("");
    lines.push("ROOT CAUSE");
    (s.root_cause?.causes || []).forEach((c, i) =>
        lines.push(`${i + 1}. ${c}`),
    );
    if (s.root_cause?.certainty_note) {
        lines.push(`Note: ${s.root_cause.certainty_note}`);
    }
    lines.push("");
    lines.push("5W1H");
    const w = s.five_w_one_h || {};
    ["who", "what", "when", "where", "why", "how"].forEach((k) => {
        if (w[k]) lines.push(`${k.toUpperCase()}: ${w[k]}`);
    });
    lines.push("");
    if (s.sentiment_and_bias) {
        lines.push("SENTIMENT & BIAS");
        lines.push(`Tone: ${s.sentiment_and_bias.tone || "Neutral"}`);
        if (s.sentiment_and_bias.tone_explanation) {
            lines.push(s.sentiment_and_bias.tone_explanation);
        }
        (s.sentiment_and_bias.bias_indicators || []).forEach((b, i) =>
            lines.push(`${i + 1}. ${b}`),
        );
        lines.push("");
    }
    lines.push(
        `CONFIDENCE: ${s.confidence_level?.level || ""} — ${s.confidence_level?.reason || ""}`,
    );
    lines.push("");
    lines.push("RECOMMENDED ACTIONS (AI-generated)");
    ["immediate", "short_term", "long_term"].forEach((k) => {
        const label = k.replace("_", "-").toUpperCase();
        const items = s.recommended_actions?.[k] || [];
        if (items.length) {
            lines.push(`  ${label}:`);
            items.forEach((it, i) => lines.push(`    ${i + 1}. ${it}`));
        }
    });
    const impactItems = s.impact_analysis?.items || [];
    if (impactItems.length) {
        lines.push("");
        lines.push("IMPACT ANALYSIS (AI-generated)");
        impactItems.forEach((it, i) =>
            lines.push(
                `${i + 1}. ${it.aspect} [${it.direction} / ${it.certainty}]: ${it.description}`,
            ),
        );
        if (s.impact_analysis?.disclaimer) {
            lines.push(`Note: ${s.impact_analysis.disclaimer}`);
        }
    }
    if (latestUpdates?.has_update) {
        lines.push("");
        lines.push("LATEST UPDATES");
        if (latestUpdates.overview) lines.push(latestUpdates.overview);
        (latestUpdates.developments || []).forEach((dv, i) =>
            lines.push(`${i + 1}. ${dv}`),
        );
        if ((latestUpdates.timeline || []).length) {
            lines.push("");
            lines.push("Timeline:");
            latestUpdates.timeline.forEach((t) =>
                lines.push(`  ${fmtShortDate(t.date)} — ${t.event}`),
            );
        }
        if (latestUpdates.current_situation) {
            lines.push("");
            lines.push(`Situasi Terkini: ${latestUpdates.current_situation}`);
        }
        if (latestUpdates.market_impact) {
            lines.push(`Dampak Pasar: ${latestUpdates.market_impact}`);
        }
        if (latestUpdates.confidence?.level) {
            lines.push(
                `Confidence: ${latestUpdates.confidence.level} — ${latestUpdates.confidence.reason || ""}`,
            );
        }
        if ((latestUpdates.sources_used || []).length) {
            lines.push("Sources:");
            latestUpdates.sources_used.forEach((u, i) =>
                lines.push(`  [${i + 1}] ${u}`),
            );
        }
    }
    lines.push("");
    lines.push("REFERENCES");
    (s.references || []).forEach((r, i) =>
        lines.push(
            `${i + 1}. ${r.title || ""} — ${r.website || ""}${r.date ? " (" + r.date + ")" : ""}\n   ${r.url || ""}`,
        ),
    );
    return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Clipboard + native Share
// ---------------------------------------------------------------------------

export async function copyToClipboard(summary, latestUpdates) {
    const text = plainSummaryText(summary, latestUpdates);
    await navigator.clipboard.writeText(text);
}

export async function shareSummary(summary, latestUpdates) {
    const shareUrl = buildShareUrl(summary);
    const text = plainSummaryText(summary, latestUpdates).slice(0, 500);
    const shareData = {
        title: summary.article?.title || "News Summary",
        text,
        url: shareUrl,
    };
    if (navigator.share) {
        await navigator.share(shareData);
        return { shared: true, url: shareUrl };
    }
    await navigator.clipboard.writeText(
        `${shareData.title}\n${shareUrl}\n\n${text}`,
    );
    return { shared: false, url: shareUrl };
}

// ---------------------------------------------------------------------------
// PDF export — Linear/Vercel-inspired clean layout
// ---------------------------------------------------------------------------

// Design tokens for PDF (points, colours as RGB).
const PDF = {
    margin: 56, // 0.78" — generous white space
    lineHeight: {
        body: 15,
        small: 13,
        heading: 20,
        title: 30,
    },
    font: {
        title: 22,
        h1: 14,
        h2: 11.5,
        body: 10.5,
        small: 9.5,
        meta: 9,
    },
    colours: {
        title: [17, 17, 17],
        heading: [17, 17, 17],
        body: [40, 40, 40],
        muted: [110, 110, 110],
        accent: [37, 99, 235], // blue-600
        divider: [230, 230, 230],
        pill: [245, 245, 245],
    },
};

function pdfBuilder() {
    const doc = new jsPDF({ unit: "pt", format: "a4" });
    const pageW = doc.internal.pageSize.getWidth();
    const pageH = doc.internal.pageSize.getHeight();
    let y = PDF.margin;
    let pageNum = 1;

    function drawFooter() {
        doc.setFont("helvetica", "normal");
        doc.setFontSize(PDF.font.meta);
        doc.setTextColor(...PDF.colours.muted);
        const footerY = pageH - 28;
        doc.text("Generated by News Summarizer", PDF.margin, footerY);
        doc.text(
            `Page ${pageNum}`,
            pageW - PDF.margin,
            footerY,
            { align: "right" },
        );
    }

    function newPage() {
        drawFooter();
        doc.addPage();
        pageNum += 1;
        y = PDF.margin;
    }

    function ensureSpace(needed) {
        if (y + needed > pageH - PDF.margin - 24) {
            newPage();
        }
    }

    function writeTitle(text) {
        doc.setFont("helvetica", "bold");
        doc.setFontSize(PDF.font.title);
        doc.setTextColor(...PDF.colours.title);
        const lines = doc.splitTextToSize(text || "Untitled", pageW - PDF.margin * 2);
        lines.forEach((line) => {
            ensureSpace(PDF.lineHeight.title);
            doc.text(line, PDF.margin, y);
            y += PDF.lineHeight.title;
        });
    }

    function writeMeta(rows) {
        doc.setFont("helvetica", "normal");
        doc.setFontSize(PDF.font.meta);
        doc.setTextColor(...PDF.colours.muted);
        rows.filter(Boolean).forEach((row) => {
            const lines = doc.splitTextToSize(row, pageW - PDF.margin * 2);
            lines.forEach((line) => {
                ensureSpace(12);
                doc.text(line, PDF.margin, y);
                y += 12;
            });
        });
    }

    function divider(top = 12, bottom = 14) {
        y += top;
        ensureSpace(4);
        doc.setDrawColor(...PDF.colours.divider);
        doc.setLineWidth(0.5);
        doc.line(PDF.margin, y, pageW - PDF.margin, y);
        y += bottom;
    }

    function sectionHeading(text) {
        y += 6;
        ensureSpace(PDF.lineHeight.heading + 10);
        // Small accent bar to the left of the heading.
        doc.setFillColor(...PDF.colours.accent);
        doc.rect(PDF.margin, y - 9, 3, 12, "F");
        doc.setFont("helvetica", "bold");
        doc.setFontSize(PDF.font.h1);
        doc.setTextColor(...PDF.colours.heading);
        doc.text(text, PDF.margin + 10, y);
        y += PDF.lineHeight.heading;
    }

    function subHeading(text) {
        ensureSpace(PDF.lineHeight.heading);
        doc.setFont("helvetica", "bold");
        doc.setFontSize(PDF.font.h2);
        doc.setTextColor(...PDF.colours.heading);
        doc.text(text, PDF.margin, y);
        y += 16;
    }

    function body(text, opts = {}) {
        if (!text) return;
        doc.setFont("helvetica", opts.bold ? "bold" : "normal");
        doc.setFontSize(opts.size || PDF.font.body);
        doc.setTextColor(...(opts.muted ? PDF.colours.muted : PDF.colours.body));
        const indent = opts.indent || 0;
        const lines = doc.splitTextToSize(
            text,
            pageW - PDF.margin * 2 - indent,
        );
        lines.forEach((line) => {
            ensureSpace(PDF.lineHeight.body);
            doc.text(line, PDF.margin + indent, y);
            y += PDF.lineHeight.body;
        });
    }

    function bulletList(items, marker = "•") {
        (items || []).filter(Boolean).forEach((it) => {
            doc.setFont("helvetica", "normal");
            doc.setFontSize(PDF.font.body);
            doc.setTextColor(...PDF.colours.body);
            const lines = doc.splitTextToSize(
                String(it),
                pageW - PDF.margin * 2 - 18,
            );
            lines.forEach((line, idx) => {
                ensureSpace(PDF.lineHeight.body);
                if (idx === 0) {
                    doc.setTextColor(...PDF.colours.muted);
                    doc.text(marker, PDF.margin + 4, y);
                    doc.setTextColor(...PDF.colours.body);
                }
                doc.text(line, PDF.margin + 18, y);
                y += PDF.lineHeight.body;
            });
        });
    }

    function numberedList(items) {
        (items || []).filter(Boolean).forEach((it, i) => {
            doc.setFont("helvetica", "normal");
            doc.setFontSize(PDF.font.body);
            doc.setTextColor(...PDF.colours.body);
            const label = String(i + 1).padStart(2, "0") + ".";
            const lines = doc.splitTextToSize(
                String(it),
                pageW - PDF.margin * 2 - 26,
            );
            lines.forEach((line, idx) => {
                ensureSpace(PDF.lineHeight.body);
                if (idx === 0) {
                    doc.setTextColor(...PDF.colours.muted);
                    doc.text(label, PDF.margin + 2, y);
                    doc.setTextColor(...PDF.colours.body);
                }
                doc.text(line, PDF.margin + 26, y);
                y += PDF.lineHeight.body;
            });
            y += 2; // breathe between items
        });
    }

    function keyValueRow(label, value) {
        if (!value) return;
        doc.setFont("helvetica", "bold");
        doc.setFontSize(PDF.font.body);
        doc.setTextColor(...PDF.colours.heading);
        const labelWidth = 78;
        ensureSpace(PDF.lineHeight.body);
        doc.text(label, PDF.margin, y);
        doc.setFont("helvetica", "normal");
        doc.setTextColor(...PDF.colours.body);
        const lines = doc.splitTextToSize(
            String(value),
            pageW - PDF.margin * 2 - labelWidth,
        );
        lines.forEach((line, idx) => {
            if (idx > 0) {
                ensureSpace(PDF.lineHeight.body);
            }
            doc.text(line, PDF.margin + labelWidth, y);
            y += PDF.lineHeight.body;
        });
        y += 2;
    }

    function link(url) {
        if (!url) return;
        doc.setFont("helvetica", "normal");
        doc.setFontSize(PDF.font.small);
        doc.setTextColor(...PDF.colours.accent);
        const lines = doc.splitTextToSize(url, pageW - PDF.margin * 2 - 18);
        lines.forEach((line, idx) => {
            ensureSpace(PDF.lineHeight.small);
            doc.textWithLink(line, PDF.margin + 18, y, { url });
            y += PDF.lineHeight.small;
        });
    }

    function finish() {
        drawFooter();
    }

    return {
        doc,
        writeTitle,
        writeMeta,
        divider,
        sectionHeading,
        subHeading,
        body,
        bulletList,
        numberedList,
        keyValueRow,
        link,
        ensureSpace,
        finish,
        get y() {
            return y;
        },
    };
}

export function exportPdf(summary, latestUpdates) {
    const b = pdfBuilder();
    const a = summary.article || {};

    // ---- Title block --------------------------------------------------------
    b.writeTitle(a.title || "Untitled");
    b.writeMeta([
        `${a.category || "General"}  ·  ~${a.reading_time_minutes || "?"} min read`,
        a.site_name ? `Source: ${a.site_name}` : null,
        a.original_url ? `URL: ${a.original_url}` : null,
        a.publication_date ? `Published: ${a.publication_date}` : null,
        `Generated: ${fmtDate(a.generated_at)}`,
        `Confidence: ${summary.confidence_level?.level || "—"}`,
    ]);
    b.divider(14, 18);

    // ---- Executive Summary --------------------------------------------------
    b.sectionHeading("Executive Summary");
    b.body(summary.executive_summary);
    b.divider();

    // ---- Key Points ---------------------------------------------------------
    b.sectionHeading("Key Points");
    b.numberedList(summary.key_points);
    b.divider();

    // ---- Main Issue ---------------------------------------------------------
    b.sectionHeading("Main Issue");
    b.body(summary.main_issue?.summary);
    if (summary.main_issue?.significance) {
        b.body(summary.main_issue.significance, { muted: true });
    }
    b.divider();

    // ---- Root Cause ---------------------------------------------------------
    b.sectionHeading("Root Cause");
    b.bulletList(summary.root_cause?.causes, "→");
    if (summary.root_cause?.certainty_note) {
        b.body(`Note: ${summary.root_cause.certainty_note}`, { muted: true });
    }
    b.divider();

    // ---- 5W1H ---------------------------------------------------------------
    b.sectionHeading("5W1H");
    const w = summary.five_w_one_h || {};
    ["who", "what", "when", "where", "why", "how"].forEach((k) => {
        if (w[k]) b.keyValueRow(k.charAt(0).toUpperCase() + k.slice(1), w[k]);
    });
    b.divider();

    // ---- Sentiment & Bias ---------------------------------------------------
    const sb = summary.sentiment_and_bias;
    if (sb && (sb.tone || sb.tone_explanation || (sb.bias_indicators || []).length)) {
        b.sectionHeading("Sentiment & Bias (AI-generated)");
        if (sb.tone) b.keyValueRow("Tone", sb.tone);
        if (sb.tone_explanation) b.body(sb.tone_explanation);
        if ((sb.bias_indicators || []).length) {
            b.body("Bias indicators:", { bold: true, size: PDF.font.h2 });
            b.bulletList(sb.bias_indicators, "•");
        }
        if (sb.disclaimer) b.body(sb.disclaimer, { muted: true, size: PDF.font.small });
        b.divider();
    }

    // ---- Confidence ---------------------------------------------------------
    b.sectionHeading("Confidence Level");
    b.keyValueRow("Level", summary.confidence_level?.level || "—");
    if (summary.confidence_level?.reason) {
        b.body(summary.confidence_level.reason, { muted: true });
    }
    b.divider();

    // ---- Recommended Actions ------------------------------------------------
    const ra = summary.recommended_actions || {};
    b.sectionHeading("Recommended Actions (AI-generated)");
    [
        ["Immediate", ra.immediate],
        ["Short-term", ra.short_term],
        ["Long-term", ra.long_term],
    ].forEach(([label, items]) => {
        if ((items || []).length) {
            b.subHeading(label);
            b.numberedList(items);
        }
    });
    if (ra.disclaimer) {
        b.body(ra.disclaimer, { muted: true, size: PDF.font.small });
    }
    b.divider();

    // ---- Impact Analysis (hidden if empty) ----------------------------------
    const impactItems = summary.impact_analysis?.items || [];
    if (impactItems.length) {
        b.sectionHeading("Impact Analysis (AI-generated)");
        impactItems.forEach((it, i) => {
            b.body(
                `${String(i + 1).padStart(2, "0")}. ${it.aspect}  [${it.direction} / ${it.certainty}]`,
                { bold: true },
            );
            b.body(it.description, { indent: 20 });
        });
        if (summary.impact_analysis?.disclaimer) {
            b.body(summary.impact_analysis.disclaimer, {
                muted: true,
                size: PDF.font.small,
            });
        }
        b.divider();
    }

    // ---- Latest Updates (only when fetched by user) -------------------------
    if (latestUpdates?.has_update) {
        b.sectionHeading("Latest Updates");
        if (latestUpdates.overview) b.body(latestUpdates.overview);

        if ((latestUpdates.developments || []).length) {
            b.subHeading("Perkembangan Terbaru");
            b.bulletList(latestUpdates.developments, "→");
        }

        if ((latestUpdates.timeline || []).length) {
            b.subHeading("Timeline");
            latestUpdates.timeline.forEach((t) => {
                b.body(`${fmtShortDate(t.date)}  —  ${t.event || ""}`, {
                    indent: 8,
                });
            });
        }

        if (latestUpdates.current_situation) {
            b.subHeading("Situasi Terkini");
            b.body(latestUpdates.current_situation);
        }

        if (latestUpdates.market_impact) {
            b.subHeading("Dampak Pasar");
            b.body(latestUpdates.market_impact);
        }

        if (latestUpdates.confidence?.level) {
            b.keyValueRow(
                "Confidence",
                `${latestUpdates.confidence.level}${
                    latestUpdates.confidence.reason
                        ? " — " + latestUpdates.confidence.reason
                        : ""
                }`,
            );
        }

        if ((latestUpdates.sources_used || []).length) {
            b.subHeading("Sources");
            latestUpdates.sources_used.forEach((u, i) => {
                b.body(`[${String(i + 1).padStart(2, "0")}]`, {
                    bold: true,
                    size: PDF.font.small,
                });
                b.link(u);
            });
        }
        b.divider();
    }

    // ---- References ---------------------------------------------------------
    if ((summary.references || []).length) {
        b.sectionHeading("References");
        summary.references.forEach((r, i) => {
            b.body(
                `[${String(i + 1).padStart(2, "0")}]  ${r.title || r.url || "Untitled"}`,
                { bold: true },
            );
            if (r.website || r.date) {
                b.body(
                    `${r.website || ""}${r.date ? "  ·  " + r.date : ""}`,
                    { muted: true, size: PDF.font.small, indent: 8 },
                );
            }
            if (r.url) b.link(r.url);
            b.body(" ", { size: 4 });
        });
    }

    b.finish();

    const slug = slugifyTitle(a.title);
    b.doc.save(`news-summary-${slug}.pdf`);
}

// ---------------------------------------------------------------------------
// DOCX export — clean layout with heading styles and consistent spacing
// ---------------------------------------------------------------------------

// Docx uses twentieths of a point ("twips") and half-points for font sizes.
// Consistent tokens across the document.
const DOCX = {
    fontFamily: "Calibri",
    monoFont: "Consolas",
    color: {
        heading: "111111",
        body: "1F2937",
        muted: "6B7280",
        accent: "2563EB",
    },
    size: {
        title: 40, // 20pt
        h1: 28, // 14pt
        h2: 24, // 12pt
        body: 22, // 11pt
        small: 20, // 10pt
        meta: 18, // 9pt
    },
    spacing: {
        section: { before: 320, after: 120 },
        sub: { before: 180, after: 80 },
        body: { after: 120 },
        tight: { after: 40 },
    },
};

function tr(text, opts = {}) {
    return new TextRun({
        text: text ?? "",
        font: { name: DOCX.fontFamily },
        color: opts.color || DOCX.color.body,
        size: opts.size || DOCX.size.body,
        bold: !!opts.bold,
        italics: !!opts.italic,
    });
}

function pTitle(text) {
    return new Paragraph({
        alignment: AlignmentType.LEFT,
        spacing: { before: 0, after: 200 },
        children: [
            new TextRun({
                text: text || "Untitled",
                font: { name: DOCX.fontFamily },
                bold: true,
                size: DOCX.size.title,
                color: DOCX.color.heading,
            }),
        ],
    });
}

function pMeta(text) {
    return new Paragraph({
        spacing: DOCX.spacing.tight,
        children: [
            new TextRun({
                text,
                font: { name: DOCX.fontFamily },
                color: DOCX.color.muted,
                size: DOCX.size.meta,
            }),
        ],
    });
}

function pSection(text) {
    return new Paragraph({
        heading: HeadingLevel.HEADING_1,
        spacing: DOCX.spacing.section,
        border: {
            bottom: {
                color: "E5E7EB",
                space: 4,
                style: BorderStyle.SINGLE,
                size: 6,
            },
        },
        children: [
            new TextRun({
                text,
                font: { name: DOCX.fontFamily },
                bold: true,
                size: DOCX.size.h1,
                color: DOCX.color.heading,
            }),
        ],
    });
}

function pSub(text) {
    return new Paragraph({
        heading: HeadingLevel.HEADING_2,
        spacing: DOCX.spacing.sub,
        children: [
            new TextRun({
                text,
                font: { name: DOCX.fontFamily },
                bold: true,
                size: DOCX.size.h2,
                color: DOCX.color.heading,
            }),
        ],
    });
}

function pBody(text, opts = {}) {
    return new Paragraph({
        spacing: DOCX.spacing.body,
        children: [tr(text, opts)],
    });
}

function pMutedNote(text) {
    return new Paragraph({
        spacing: DOCX.spacing.body,
        children: [tr(text, { color: DOCX.color.muted, size: DOCX.size.small, italic: true })],
    });
}

function pKeyValue(label, value) {
    return new Paragraph({
        spacing: DOCX.spacing.tight,
        children: [
            tr(`${label}: `, { bold: true, color: DOCX.color.heading }),
            tr(String(value)),
        ],
    });
}

function pNumbered(text, index) {
    return new Paragraph({
        spacing: DOCX.spacing.tight,
        indent: { left: 360, hanging: 260 },
        children: [
            tr(`${String(index + 1).padStart(2, "0")}. `, {
                bold: true,
                color: DOCX.color.muted,
            }),
            tr(text),
        ],
    });
}

function pBullet(text, marker = "•") {
    return new Paragraph({
        spacing: DOCX.spacing.tight,
        indent: { left: 360, hanging: 260 },
        children: [
            tr(`${marker}  `, { color: DOCX.color.muted, bold: true }),
            tr(text),
        ],
    });
}

function pLink(url) {
    return new Paragraph({
        spacing: DOCX.spacing.tight,
        indent: { left: 360 },
        children: [
            tr(url, { color: DOCX.color.accent, size: DOCX.size.small }),
        ],
    });
}

export async function exportDocx(summary, latestUpdates) {
    const a = summary.article || {};
    const children = [];

    // Title block
    children.push(pTitle(a.title || "Untitled"));
    children.push(
        pMeta(
            `${a.category || "General"}   ·   ~${a.reading_time_minutes || "?"} min read`,
        ),
    );
    if (a.site_name) children.push(pMeta(`Source: ${a.site_name}`));
    if (a.original_url) children.push(pMeta(`URL: ${a.original_url}`));
    if (a.publication_date)
        children.push(pMeta(`Published: ${a.publication_date}`));
    children.push(pMeta(`Generated: ${fmtDate(a.generated_at)}`));
    children.push(
        pMeta(`Confidence: ${summary.confidence_level?.level || "—"}`),
    );

    // Executive Summary
    children.push(pSection("Executive Summary"));
    children.push(pBody(summary.executive_summary || ""));

    // Key Points
    children.push(pSection("Key Points"));
    (summary.key_points || []).forEach((k, i) =>
        children.push(pNumbered(String(k), i)),
    );

    // Main Issue
    children.push(pSection("Main Issue"));
    if (summary.main_issue?.summary) children.push(pBody(summary.main_issue.summary));
    if (summary.main_issue?.significance) {
        children.push(pMutedNote(summary.main_issue.significance));
    }

    // Root Cause
    children.push(pSection("Root Cause"));
    (summary.root_cause?.causes || []).forEach((c) =>
        children.push(pBullet(String(c), "→")),
    );
    if (summary.root_cause?.certainty_note) {
        children.push(pMutedNote(`Note: ${summary.root_cause.certainty_note}`));
    }

    // 5W1H
    children.push(pSection("5W1H"));
    const w = summary.five_w_one_h || {};
    ["who", "what", "when", "where", "why", "how"].forEach((k) => {
        if (w[k])
            children.push(
                pKeyValue(k.charAt(0).toUpperCase() + k.slice(1), w[k]),
            );
    });

    // Sentiment & Bias
    const sb = summary.sentiment_and_bias;
    if (sb && (sb.tone || sb.tone_explanation || (sb.bias_indicators || []).length)) {
        children.push(pSection("Sentiment & Bias (AI-generated)"));
        if (sb.tone) children.push(pKeyValue("Tone", sb.tone));
        if (sb.tone_explanation) children.push(pBody(sb.tone_explanation));
        if ((sb.bias_indicators || []).length) {
            children.push(pSub("Bias indicators"));
            sb.bias_indicators.forEach((bi) => children.push(pBullet(String(bi))));
        }
        if (sb.disclaimer) children.push(pMutedNote(sb.disclaimer));
    }

    // Confidence
    children.push(pSection("Confidence Level"));
    children.push(pKeyValue("Level", summary.confidence_level?.level || "—"));
    if (summary.confidence_level?.reason) {
        children.push(pMutedNote(summary.confidence_level.reason));
    }

    // Recommended Actions
    const ra = summary.recommended_actions || {};
    children.push(pSection("Recommended Actions (AI-generated)"));
    [
        ["Immediate", ra.immediate],
        ["Short-term", ra.short_term],
        ["Long-term", ra.long_term],
    ].forEach(([label, items]) => {
        if ((items || []).length) {
            children.push(pSub(label));
            items.forEach((it, i) => children.push(pNumbered(String(it), i)));
        }
    });
    if (ra.disclaimer) children.push(pMutedNote(ra.disclaimer));

    // Impact Analysis (hidden if empty)
    const impactItems = summary.impact_analysis?.items || [];
    if (impactItems.length) {
        children.push(pSection("Impact Analysis (AI-generated)"));
        impactItems.forEach((it, i) => {
            children.push(
                new Paragraph({
                    spacing: DOCX.spacing.tight,
                    children: [
                        tr(
                            `${String(i + 1).padStart(2, "0")}. ${it.aspect}  `,
                            { bold: true, color: DOCX.color.heading },
                        ),
                        tr(`[${it.direction} / ${it.certainty}]`, {
                            color: DOCX.color.muted,
                            size: DOCX.size.small,
                        }),
                    ],
                }),
            );
            children.push(
                new Paragraph({
                    spacing: DOCX.spacing.body,
                    indent: { left: 360 },
                    children: [tr(String(it.description || ""))],
                }),
            );
        });
        if (summary.impact_analysis?.disclaimer) {
            children.push(pMutedNote(summary.impact_analysis.disclaimer));
        }
    }

    // Latest Updates (only when fetched by user)
    if (latestUpdates?.has_update) {
        children.push(pSection("Latest Updates"));
        if (latestUpdates.overview) children.push(pBody(latestUpdates.overview));

        if ((latestUpdates.developments || []).length) {
            children.push(pSub("Perkembangan Terbaru"));
            latestUpdates.developments.forEach((dv) =>
                children.push(pBullet(String(dv), "→")),
            );
        }

        if ((latestUpdates.timeline || []).length) {
            children.push(pSub("Timeline"));
            latestUpdates.timeline.forEach((t) => {
                children.push(
                    new Paragraph({
                        spacing: DOCX.spacing.tight,
                        indent: { left: 360, hanging: 260 },
                        children: [
                            tr(`${fmtShortDate(t.date)}   `, {
                                bold: true,
                                color: DOCX.color.muted,
                                size: DOCX.size.small,
                            }),
                            tr(String(t.event || "")),
                        ],
                    }),
                );
            });
        }

        if (latestUpdates.current_situation) {
            children.push(pSub("Situasi Terkini"));
            children.push(pBody(latestUpdates.current_situation));
        }

        if (latestUpdates.market_impact) {
            children.push(pSub("Dampak Pasar"));
            children.push(pBody(latestUpdates.market_impact));
        }

        if (latestUpdates.confidence?.level) {
            children.push(
                pKeyValue(
                    "Confidence",
                    `${latestUpdates.confidence.level}${
                        latestUpdates.confidence.reason
                            ? " — " + latestUpdates.confidence.reason
                            : ""
                    }`,
                ),
            );
        }

        if ((latestUpdates.sources_used || []).length) {
            children.push(pSub("Sources"));
            latestUpdates.sources_used.forEach((u, i) => {
                children.push(
                    new Paragraph({
                        spacing: DOCX.spacing.tight,
                        indent: { left: 360 },
                        children: [
                            tr(`[${String(i + 1).padStart(2, "0")}]  `, {
                                bold: true,
                                color: DOCX.color.muted,
                                size: DOCX.size.small,
                            }),
                            tr(u, {
                                color: DOCX.color.accent,
                                size: DOCX.size.small,
                            }),
                        ],
                    }),
                );
            });
        }
    }

    // References
    if ((summary.references || []).length) {
        children.push(pSection("References"));
        summary.references.forEach((r, i) => {
            children.push(
                new Paragraph({
                    spacing: DOCX.spacing.tight,
                    children: [
                        tr(`[${String(i + 1).padStart(2, "0")}]  `, {
                            bold: true,
                            color: DOCX.color.muted,
                        }),
                        tr(r.title || r.url || "Untitled", { bold: true }),
                    ],
                }),
            );
            if (r.website || r.date) {
                children.push(
                    new Paragraph({
                        spacing: DOCX.spacing.tight,
                        indent: { left: 360 },
                        children: [
                            tr(
                                `${r.website || ""}${r.date ? "  ·  " + r.date : ""}`,
                                { color: DOCX.color.muted, size: DOCX.size.small },
                            ),
                        ],
                    }),
                );
            }
            if (r.url) children.push(pLink(r.url));
        });
    }

    const doc = new Document({
        creator: "News Summarizer",
        title: a.title || "News Summary",
        styles: {
            default: {
                document: {
                    run: {
                        font: DOCX.fontFamily,
                        size: DOCX.size.body,
                        color: DOCX.color.body,
                    },
                    paragraph: { spacing: { line: 300 } },
                },
            },
        },
        sections: [
            {
                properties: {
                    page: {
                        margin: {
                            top: 1000,
                            bottom: 1000,
                            left: 1100,
                            right: 1100,
                        },
                    },
                },
                children,
            },
        ],
    });

    const blob = await Packer.toBlob(doc);
    const slug = slugifyTitle(a.title);
    saveAs(blob, `news-summary-${slug}.docx`);
}
