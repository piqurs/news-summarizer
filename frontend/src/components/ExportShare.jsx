import { Copy, FileDown, FileText, Share2 } from "lucide-react";
import { toast } from "sonner";
import {
    copyToClipboard,
    exportDocx,
    exportPdf,
    shareSummary,
} from "../lib/exporters";

export function ExportShare({ summary }) {
    const handleCopy = async () => {
        try {
            await copyToClipboard(summary);
            toast.success("Summary copied to clipboard");
        } catch {
            toast.error("Could not copy to clipboard");
        }
    };
    const handlePdf = () => {
        try {
            exportPdf(summary);
            toast.success("PDF exported");
        } catch {
            toast.error("PDF export failed");
        }
    };
    const handleDocx = async () => {
        try {
            await exportDocx(summary);
            toast.success("Word document exported");
        } catch {
            toast.error("DOCX export failed");
        }
    };
    const handleShare = async () => {
        try {
            const shared = await shareSummary(summary);
            toast.success(shared ? "Shared" : "Copied — sharing not available");
        } catch {
            /* user cancelled — ignore */
        }
    };

    const btn = "inline-flex items-center gap-1.5 rounded-md border border-border " +
        "px-3 py-1.5 text-xs font-medium text-muted-foreground " +
        "transition-colors hover:text-foreground hover:border-foreground/30 " +
        "focus:outline-none focus:ring-2 focus:ring-accent";

    return (
        <div className="flex flex-wrap items-center gap-2" data-testid="export-share">
            <button onClick={handleCopy} className={btn} data-testid="btn-copy">
                <Copy className="h-3.5 w-3.5" strokeWidth={1.75} /> Copy
            </button>
            <button onClick={handlePdf} className={btn} data-testid="btn-pdf">
                <FileDown className="h-3.5 w-3.5" strokeWidth={1.75} /> PDF
            </button>
            <button onClick={handleDocx} className={btn} data-testid="btn-docx">
                <FileText className="h-3.5 w-3.5" strokeWidth={1.75} /> Word
            </button>
            <button onClick={handleShare} className={btn} data-testid="btn-share">
                <Share2 className="h-3.5 w-3.5" strokeWidth={1.75} /> Share
            </button>
        </div>
    );
}
