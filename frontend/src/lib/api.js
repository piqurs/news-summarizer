import axios from "axios";

const BASE = process.env.REACT_APP_BACKEND_URL;
const API = `${BASE}/api`;

const client = axios.create({
    baseURL: API,
    timeout: 120000,
    headers: { "Content-Type": "application/json" },
});

export async function summarizeUrl(url) {
    const { data } = await client.post("/summarize", { url });
    return data;
}

export async function getLatestUpdates(topic, sourceUrl) {
    const { data } = await client.post("/latest-updates", {
        topic,
        source_url: sourceUrl,
    });
    return data;
}

export async function getRateStatus() {
    const { data } = await client.get("/rate-status");
    return data;
}
