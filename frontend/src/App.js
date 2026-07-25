import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/lib/theme";
import Home from "@/pages/Home";

function App() {
    return (
        <ThemeProvider>
            <BrowserRouter>
                <Routes>
                    <Route path="/" element={<Home />} />
                    <Route path="*" element={<Home />} />
                </Routes>
            </BrowserRouter>
            <Toaster
                position="top-right"
                richColors
                closeButton
                toastOptions={{
                    style: { fontFamily: "IBM Plex Sans, sans-serif" },
                }}
            />
        </ThemeProvider>
    );
}

export default App;
