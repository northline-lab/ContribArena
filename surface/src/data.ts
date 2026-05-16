import type { SurfaceData } from "./types";

const DATA_PATH = import.meta.env.BASE_URL + "data/surface.json";
const API_BASE = (import.meta.env.VITE_CONTRIBARENA_API_BASE_URL ?? "").replace(/\/$/, "");

export async function loadSurfaceData(): Promise<SurfaceData> {
  const res = await fetch(API_BASE ? `${API_BASE}/api/surface` : DATA_PATH);
  if (!res.ok) throw new Error(`Failed to load surface data: ${res.status}`);
  return res.json();
}

export function dataSourceLabel(): string {
  return API_BASE || "static bundle";
}
