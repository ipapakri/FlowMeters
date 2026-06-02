/**
 * Centralised Axios instance.
 * All requests carry cookies (withCredentials) so the httpOnly JWT is sent automatically.
 */

import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  withCredentials: true,
});

// ── Types ────────────────────────────────────────────────────────────────────

export interface Device {
  id: number;
  name: string;
  serialNo: string;
  productName: string | null;
  groupName: string | null;
  fetchedAt: string | null;
}

export interface DashboardResponse {
  devices: Device[];
  points: DashboardPoint[];
}

export interface RealtimeResponse {
  devices: Device[];
  points: DashboardPoint[];
}

export interface DashboardPoint {
  deviceId: number | null;
  deviceName: string | null;
  serialNo: string | null;
  paramName: string | null;
  flow: number | null;
  instantaneousFlow: number | null;
  instantaneousVelocity: number | null;
  waterTemperature: number | null;
  accumulatedCooling: number | null;
  heat: number | null;
  units?: Partial<
    Record<
      | "flow"
      | "instantaneousFlow"
      | "instantaneousVelocity"
      | "waterTemperature"
      | "accumulatedCooling"
      | "heat",
      string | null | undefined
    >
  >;
  issueDate: string | null;
  fetchedAt: string;
}

export interface CumulativePoint {
  deviceId: number;
  deviceName: string | null;
  serialNo: string | null;
  paramName: string | null;
  issueDate: string | null;
  flow: number | null;
  instantaneousFlow: number | null;
  instantaneousVelocity: number | null;
  waterTemperature: number | null;
  returnWaterTemperature: number | null;
  accumulatedCooling: number | null;
  heat: number | null;
  fetchedAt: string;
}

// ── Auth ─────────────────────────────────────────────────────────────────────

export async function login(username: string, password: string) {
  const res = await api.post<{ username: string }>("/auth/login", {
    username,
    password,
  });
  return res.data;
}

export async function logout() {
  await api.post("/auth/logout");
}

export async function getMe() {
  const res = await api.get<{ username: string }>("/auth/me");
  return res.data;
}

// ── Devices ──────────────────────────────────────────────────────────────────

export async function getDevices(): Promise<Device[]> {
  const res = await api.get<Device[]>("/devices");
  return res.data;
}

// ── Dashboard ────────────────────────────────────────────────────────────────

export async function getDashboard(
  params: { paramName?: string; category?: number } = {}
): Promise<DashboardResponse> {
  const res = await api.get<DashboardResponse>("/dashboard", { params });
  console.log(`dashboard response: ${JSON.stringify(res.data, null, 2)}`);
  return res.data;
}

export async function getRealtime(params: { deviceId?: number } = {}): Promise<RealtimeResponse> {
  const res = await api.get<RealtimeResponse>("/realtime", { params });
  return res.data;
}

// ── Cumulative ───────────────────────────────────────────────────────────────

export interface CumulativeParams {
  paramName?: string;
  category?: number;
}

export async function getCumulative(
  deviceId: number,
  params: CumulativeParams = {}
): Promise<CumulativePoint[]> {
  const res = await api.get<CumulativePoint[]>(`/cumulative/${deviceId}`, {
    params,
  });
  return res.data;
}
