/**
 * DashboardChart — hourly total flow (DI+ positive accumulation) for all devices.
 * Data from GET /api/dashboard → wm/device/dashboard/data/list.
 */

import ReactECharts from "echarts-for-react";
import { useMemo } from "react";
import type { DashboardPoint } from "../api";

interface Props {
  data: DashboardPoint[];
  loading: boolean;
  error: string | null;
  paramName: string;
  onParamNameChange: (p: string) => void;
  category: number;
  onCategoryChange: (c: number) => void;
}

function pointTimestamp(pt: DashboardPoint): string {
  return pt.issueDate ?? pt.fetchedAt;
}

export function DashboardChart({
  data,
  loading,
  error,
  paramName,
  onParamNameChange,
  category,
  onCategoryChange,
}: Props) {
  const paramLabel = data[0]?.paramName ?? "DI+";
  const quantityLabel = useMemo(() => {
    const p = (paramName || "DI+").toUpperCase();
    if (p === "DI+") return "Positive Accumulation";
    if (p === "THEAT") return "Cumulative Heating Volume";
    if (p === "TH") return "Cumulative Cooling Volume";
    return p;
  }, [paramName]);

  const timeLabel = useMemo(() => {
    if (category === 4) return "Past Year";
    if (category === 3) return "Past Month";
    if (category === 1) return "Today";
    if (category === 2) return "Yesterday";
    return `Category ${category}`;
  }, [category]);

  const option = useMemo(() => {
    const sorted = [...data].sort(
      (a, b) =>
        new Date(pointTimestamp(a)).getTime() - new Date(pointTimestamp(b)).getTime()
    );

    const seriesName = sorted[0]?.deviceName ?? `All devices — ${paramLabel}`;

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1e293b",
        borderColor: "#334155",
        textStyle: { color: "#e2e8f0" },
        formatter: (params: unknown[]) => {
          const pts = params as { seriesName: string; value: [string, number | null]; marker: string }[];
          if (!pts.length) return "";
          const time = new Date(pts[0].value[0]).toLocaleString();
          const lines = pts.map(
            (p) =>
              `${p.marker} ${p.seriesName}: <b>${p.value[1] != null ? p.value[1].toFixed(3) : "—"} m³</b>`
          );
          return `${time}<br/>${lines.join("<br/>")}`;
        },
      },
      legend: {
        bottom: 0,
        textStyle: { color: "#94a3b8" },
      },
      grid: { top: 16, left: 60, right: 20, bottom: 48 },
      xAxis: {
        type: "time",
        axisLabel: { color: "#94a3b8", fontSize: 11 },
        axisLine: { lineStyle: { color: "#334155" } },
        splitLine: { lineStyle: { color: "#1e293b" } },
      },
      yAxis: {
        type: "value",
        name: "m³",
        nameTextStyle: { color: "#64748b", fontSize: 11 },
        axisLabel: { color: "#94a3b8", fontSize: 11 },
        axisLine: { lineStyle: { color: "#334155" } },
        splitLine: { lineStyle: { color: "#1e293b" } },
      },
      series: [
        {
          name: seriesName,
          type: "bar",
          data: sorted.map((pt) => [pointTimestamp(pt), pt.flow]),
          itemStyle: { color: "#3b82f6", borderRadius: [3, 3, 0, 0] },
          emphasis: { itemStyle: { color: "#60a5fa" } },
        },
      ],
    };
  }, [data, paramLabel]);

  return (
    <div className="bg-slate-800/60 border border-slate-700/50 rounded-2xl p-6">
      <div className="flex flex-wrap items-center gap-3 mb-5">
        <div className="flex-1 min-w-0">
          <h2 className="text-white font-semibold">Total flow — all devices</h2>
          <p className="text-slate-400 text-sm mt-0.5">
            {quantityLabel} ({paramLabel}) — {timeLabel}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">Quantity</span>
          <select
            value={paramName}
            onChange={(e) => onParamNameChange(e.target.value)}
            className="bg-slate-700 text-slate-100 text-xs rounded-lg px-3 py-2 border border-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
          >
            <option value="DI+">Positive Accumulation (DI+)</option>
            <option value="THeat">Cumulative Heating Volume (THeat)</option>
            <option value="TH">Cumulative Cooling Volume (TH)</option>
          </select>
        </div>

        <div className="flex rounded-lg overflow-hidden border border-slate-600">
          {[
            { category: 4, label: "Past Year" },
            { category: 3, label: "Past Month" },
            { category: 1, label: "Today" },
            { category: 2, label: "Yesterday" },
          ].map((opt) => (
            <button
              key={opt.category}
              onClick={() => onCategoryChange(opt.category)}
              className={`px-3 py-1.5 text-xs font-medium transition ${
                category === opt.category
                  ? "bg-blue-600 text-white"
                  : "bg-slate-700 text-slate-400 hover:bg-slate-600"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="h-72 flex items-center justify-center text-slate-400">
          <svg className="animate-spin w-6 h-6 mr-2" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          Loading data…
        </div>
      ) : error ? (
        <div className="h-72 flex flex-col items-center justify-center text-slate-400 text-sm gap-2">
          <div className="text-red-300 font-medium">{error}</div>
          <div className="text-slate-500">Try a different window or refresh.</div>
        </div>
      ) : data.length === 0 ? (
        <div className="h-72 flex items-center justify-center text-slate-500 text-sm">
          No hourly totals returned for the selected window.
        </div>
      ) : (
        <ReactECharts
          option={option}
          style={{ height: 320 }}
          opts={{ renderer: "canvas" }}
        />
      )}
    </div>
  );
}
