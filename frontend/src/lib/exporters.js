import { jsPDF } from "jspdf";
import {
    Document,
    Packer,
    Paragraph,
    HeadingLevel,
    TextRun,
    AlignmentType,
} from "docx";
import { saveAs } from "file-saver";

function fmtDate(iso) {
    if (!iso) return "";
    try {
        return new Date(iso).toLocaleString();
    } catch {
        return iso;
    }
}

function plainSummaryText(s) {
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
    lines.push("RECOMMENDED ACTIONS (AI-generated)");
    ["immediate", "short_term", "long_term"].forEach((k) => {
        const label = k.replace("_", "-").toUpperCase();
        const items = s.recommended_actions?.[k] || [];
        if (items.length) {
            lines.push(`  ${label}:`);
            items.forEach((it, i) => lines.push(`    ${i + 1}. ${it}`));
        }
    });
    lines.push("");
    lines.push("5W1H");
    const w = s.five_w_one_h || {};
    ["who", "what", "when", "where", "why", "how"].forEach((k) => {
        if (w[k]) lines.push(`${k.toUpperCase()}: ${w[k]}`);
    });
    lines.push("");
    lines.push("REFERENCES");
    (s.references || []).forEach((r, i) =>
        lines.push(
            `${i + 1}. ${r.title || ""} — ${r.website || ""}${r.date ? " (" + r.date + ")" : ""}\n   ${r.url || ""}`,
        ),
    );
    lines.push("");
    lines.push(
        `CONFIDENCE: ${s.confidence_level?.level || ""} — ${s.confidence_level?.reason || ""}`,
    );
    return lines.join("\n");
}

export async function copyToClipboard(summary) {
    const text = plainSummaryText(summary);
    await navigator.clipboard.writeText(text);
}

export async function shareSummary(summary) {
    const text = plainSummaryText(summary).slice(0, 500);
    const shareData = {
        title: summary.article?.title || "News Summary",
        text,
        url: summary.article?.original_url,
    };
    if (navigator.share) {
        await navigator.share(shareData);
        return true;
    }
    await navigator.clipboard.writeText(
        `${shareData.title}\n${shareData.url}\n\n${text}`,
    );
    return false;
}

export function exportPdf(summary) {
    const doc = new jsPDF({ unit: "pt", format: "a4" });
    const pageW = doc.internal.pageSize.getWidth();
    const pageH = doc.internal.pageSize.getHeight();
    const margin = 48;
    let y = margin;

    function ensureSpace(needed) {
        if (y + needed > pageH - margin) {
            doc.addPage();
            y = margin;
        }
    }
    function writeHeading(text) {
        ensureSpace(28);
        doc.setFont("helvetica", "bold");
        doc.setFontSize(13);
        doc.setTextColor(20, 20, 20);
        doc.text(text, margin, y);
        y += 20;
    }
    function writeBody(text, opts = {}) {
        doc.setFont("helvetica", "normal");
        doc.setFontSize(opts.size || 10.5);
        doc.setTextColor(50, 50, 50);
        const lines = doc.splitTextToSize(text || "", pageW - margin * 2);
        for (const line of lines) {
            ensureSpace(14);
            doc.text(line, margin, y);
            y += 14;
        }
    }
    function writeList(items) {
        (items || []).forEach((it, i) => writeBody(`${i + 1}. ${it}`));
    }
    function divider() {
        ensureSpace(20);
        doc.setDrawColor(200);
        doc.line(margin, y, pageW - margin, y);
        y += 14;
    }

    const a = summary.article || {};
    // Title
    doc.setFont("helvetica", "bold");
    doc.setFontSize(20);
    doc.setTextColor(15, 15, 15);
    const titleLines = doc.splitTextToSize(a.title || "Untitled", pageW - margin * 2);
    titleLines.forEach((l) => {
        ensureSpace(24);
        doc.text(l, margin, y);
        y += 24;
    });
    y += 4;
    writeBody(
        `Category: ${a.category || ""}   |   ~${a.reading_time_minutes || "?"} min read`,
    );
    writeBody(`Source: ${a.original_url || ""}`);
    if (a.publication_date) writeBody(`Published: ${a.publication_date}`);
    writeBody(`Generated: ${fmtDate(a.generated_at)}`);
    divider();

    writeHeading("Executive Summary");
    writeBody(summary.executive_summary);
    divider();

    writeHeading("Key Points");
    writeList(summary.key_points);
    divider();

    writeHeading("Main Issue");
    writeBody(summary.main_issue?.summary);
    if (summary.main_issue?.significance)
        writeBody(`Significance: ${summary.main_issue.significance}`);
    divider();

    writeHeading("Root Cause");
    writeList(summary.root_cause?.causes);
    if (summary.root_cause?.certainty_note)
        writeBody(`Note: ${summary.root_cause.certainty_note}`);
    divider();

    writeHeading("Recommended Actions (AI-generated)");
    ["immediate", "short_term", "long_term"].forEach((k) => {
        const items = summary.recommended_actions?.[k] || [];
        if (items.length) {
            writeBody(k.replace("_", "-").toUpperCase() + ":");
            writeList(items);
        }
    });
    divider();

    writeHeading("5W1H");
    const w = summary.five_w_one_h || {};
    ["who", "what", "when", "where", "why", "how"].forEach((k) => {
        if (w[k]) writeBody(`${k.toUpperCase()}: ${w[k]}`);
    });
    divider();

    writeHeading("References");
    (summary.references || []).forEach((r, i) => {
        writeBody(
            `${i + 1}. ${r.title || ""} — ${r.website || ""}${r.date ? " (" + r.date + ")" : ""}`,
        );
        writeBody(`   ${r.url || ""}`);
    });
    divider();

    writeHeading("Confidence Level");
    writeBody(
        `${summary.confidence_level?.level || ""} — ${summary.confidence_level?.reason || ""}`,
    );

    const filename = `news-summary-${(a.title || "untitled").slice(0, 40).replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.pdf`;
    doc.save(filename);
}

export async function exportDocx(summary) {
    const a = summary.article || {};
    const H = (text) =>
        new Paragraph({
            heading: HeadingLevel.HEADING_2,
            spacing: { before: 240, after: 80 },
            children: [new TextRun({ text, bold: true })],
        });
    const P = (text, opts = {}) =>
        new Paragraph({
            spacing: { after: 80 },
            children: [new TextRun({ text: text || "", ...opts })],
        });
    const L = (items) =>
        (items || []).map(
            (it, i) =>
                new Paragraph({
                    spacing: { after: 40 },
                    children: [new TextRun({ text: `${i + 1}. ${it}` })],
                }),
        );

    const children = [
        new Paragraph({
            heading: HeadingLevel.TITLE,
            alignment: AlignmentType.LEFT,
            children: [new TextRun({ text: a.title || "Untitled", bold: true })],
        }),
        P(
            `Category: ${a.category || ""}   |   ~${a.reading_time_minutes || "?"} min read`,
        ),
        P(`Source: ${a.original_url || ""}`),
        ...(a.publication_date ? [P(`Published: ${a.publication_date}`)] : []),
        P(`Generated: ${fmtDate(a.generated_at)}`),
        H("Executive Summary"),
        P(summary.executive_summary),
        H("Key Points"),
        ...L(summary.key_points),
        H("Main Issue"),
        P(summary.main_issue?.summary),
        ...(summary.main_issue?.significance
            ? [P(`Significance: ${summary.main_issue.significance}`)]
            : []),
        H("Root Cause"),
        ...L(summary.root_cause?.causes),
        ...(summary.root_cause?.certainty_note
            ? [P(`Note: ${summary.root_cause.certainty_note}`)]
            : []),
        H("Recommended Actions (AI-generated)"),
    ];

    ["immediate", "short_term", "long_term"].forEach((k) => {
        const items = summary.recommended_actions?.[k] || [];
        if (items.length) {
            children.push(P(k.replace("_", "-").toUpperCase() + ":", { bold: true }));
            children.push(...L(items));
        }
    });

    children.push(H("5W1H"));
    const w = summary.five_w_one_h || {};
    ["who", "what", "when", "where", "why", "how"].forEach((k) => {
        if (w[k]) children.push(P(`${k.toUpperCase()}: ${w[k]}`));
    });

    children.push(H("References"));
    (summary.references || []).forEach((r, i) => {
        children.push(
            P(
                `${i + 1}. ${r.title || ""} — ${r.website || ""}${r.date ? " (" + r.date + ")" : ""}`,
            ),
        );
        children.push(P(`   ${r.url || ""}`));
    });

    children.push(H("Confidence Level"));
    children.push(
        P(
            `${summary.confidence_level?.level || ""} — ${summary.confidence_level?.reason || ""}`,
        ),
    );

    const doc = new Document({ sections: [{ children }] });
    const blob = await Packer.toBlob(doc);
    const filename = `news-summary-${(a.title || "untitled").slice(0, 40).replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.docx`;
    saveAs(blob, filename);
}
