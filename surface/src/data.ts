import type { SurfaceData } from "./types";

const DATA_PATH = import.meta.env.BASE_URL + "data/surface.json";

export async function loadSurfaceData(): Promise<SurfaceData> {
  const res = await fetch(DATA_PATH);
  if (!res.ok) throw new Error(`Failed to load surface data: ${res.status}`);
  return res.json();
}
