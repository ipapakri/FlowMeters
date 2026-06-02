import { useCallback, useEffect, useMemo, useState } from "react";
import { getDashboard } from "../api";
import type { Device, DashboardPoint } from "../api";
import { CumulativeChart } from "../components/CumulativeChart";
import { DashboardChart } from "../components/DashboardChart";
import { DeviceSelect } from "../components/DeviceSelect";

interface Props {
  username: string;
  onLogout: () => void;
}

function categoryLabel(category: number): string {
  if (category === 4) return "Past Year";
  if (category === 3) return "Past Month";
  if (category === 1) return "Today";
  if (category === 2) return "Yesterday";
  return `Category ${category}`;
}

export function DashboardPage({ username, onLogout }: Props) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [allDashboardData, setAllDashboardData] = useState<DashboardPoint[]>([]);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const [paramName, setParamName] = useState("DI+");
  const [category, setCategory] = useState(1);

  const [selected, setSelected] = useState<"all" | number>("all");

  const dashboardData = useMemo(() => allDashboardData, [allDashboardData]);

  const refreshDashboard = useCallback(() => {
    if (selected !== "all") return;
    setDashboardError(null);
    setDashboardLoading(true);
    getDashboard({ paramName, category })
      .then(({ devices: deviceList, points }) => {
        setDevices(deviceList);
        setAllDashboardData(points);
      })
      .catch((err) => {
        console.error(err);
        setAllDashboardData([]);
        setDashboardError("Failed to load dashboard data for the selected window.");
      })
      .finally(() => setDashboardLoading(false));
  }, [category, paramName, selected]);

  useEffect(() => {
    refreshDashboard();
  }, [refreshDashboard]);

  const selectedDevice = devices.find((d) => d.id === selected);

  return (
    <div className="min-h-screen bg-slate-900 text-white">
      {/* Top bar */}
      <header className="bg-slate-800/80 backdrop-blur border-b border-slate-700/50 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-4">
          {/* Left cluster */}
          <div className="flex items-center gap-4 min-w-0 flex-1">
            {/* Brand */}
            <div className="flex items-center gap-2 shrink-0">
              <svg
                className="w-6 h-6 text-blue-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
                />
              </svg>
              <span className="font-bold text-white hidden sm:block">
                FlowMeters
              </span>
            </div>

            {/* Device selector */}
            <div className="flex-1 min-w-0 sm:flex-none">
              <DeviceSelect
                devices={devices}
                selected={selected}
                onChange={setSelected}
                loading={dashboardLoading && selected === "all"}
              />
            </div>

            {/* Device count badge */}
            {devices.length > 0 && (
              <span className="hidden sm:inline text-xs text-slate-500 bg-slate-700/50 px-2 py-0.5 rounded-full shrink-0">
                {devices.length} device{devices.length !== 1 ? "s" : ""}
              </span>
            )}
          </div>

          {/* Right cluster */}
          <div className="flex items-center gap-2 shrink-0">
            {/* Refresh button */}
            <button
              onClick={selected === "all" ? refreshDashboard : undefined}
              title="Refresh"
              className="p-2 text-slate-400 hover:text-white transition rounded-lg hover:bg-slate-700/50"
            >
              <svg
                className="w-4 h-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
            </button>

            {/* User / logout */}
            <div className="flex items-center gap-2">
              <span className="text-sm text-slate-400 hidden sm:block">
                {username}
              </span>
              <button
                onClick={onLogout}
                className="text-sm text-slate-400 hover:text-red-400 transition px-3 py-1.5 rounded-lg hover:bg-red-500/10"
              >
                Sign out
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 py-6">
        {selected === "all" ? (
          <>
            {/* Stats row */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
              <StatCard
                label="Devices"
                value={devices.length}
                icon="📡"
              />
              <StatCard
                label="Hourly readings"
                value={dashboardData.length}
                icon="📊"
              />
              <StatCard
                label="Window"
                value={categoryLabel(category)}
                icon="🕐"
              />
              <StatCard
                label="Last update"
                value={
                  dashboardData.length
                    ? new Date(
                        dashboardData[dashboardData.length - 1].issueDate ??
                          dashboardData[dashboardData.length - 1].fetchedAt
                      ).toLocaleTimeString()
                    : "—"
                }
                icon="🔄"
              />
            </div>

            <DashboardChart
              data={dashboardData}
              loading={dashboardLoading}
              error={dashboardError}
              paramName={paramName}
              onParamNameChange={setParamName}
              category={category}
              onCategoryChange={setCategory}
            />
          </>
        ) : selectedDevice ? (
          <CumulativeChart
            deviceId={selectedDevice.id}
            deviceName={selectedDevice.name ?? `Device ${selectedDevice.id}`}
          />
        ) : (
          <div className="flex items-center justify-center h-64 text-slate-500">
            Select a device to view its data.
          </div>
        )}
      </main>
    </div>
  );
}

function StatCard({
  label,
  value,
  icon,
}: {
  label: string;
  value: string | number;
  icon: string;
}) {
  return (
    <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4">
      <div className="text-2xl mb-1">{icon}</div>
      <div className="text-lg font-bold text-white">{value}</div>
      <div className="text-xs text-slate-400 mt-0.5">{label}</div>
    </div>
  );
}
