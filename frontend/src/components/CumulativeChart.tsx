/**
 * CumulativeChart — shows historical cumulative data for a single device.
 * Fetches from /api/cumulative/{deviceId} with paramName + category.
 */

import ReactECharts from "echarts-for-react";
import { useEffect, useMemo, useState } from "react";
import { getCumulative } from "../api";
import type { CumulativePoint } from "../api";

const PARAM_OPTIONS = [
  { value: "DI+", label: "DI+ — Positive accumulation" },
  { value: "DI-", label: "DI- — Negative accumulation" },
  { value: "DIN", label: "DIN — Net accumulation" },
  { value: "DQ", label: "DQ — Instantaneous flow" },
  { value: "DV", label: "DV — Instantaneous velocity" },
  { value: "TI", label: "TI — Inlet water temp" },
  { value: "TO", label: "TO — Outlet water temp" },
  { value: "TH", label: "TH — Accumulated cooling" },
  { value: "RH", label: "RH — Instantaneous cooling" },
  { value: "EQH", label: "EQH — Instantaneous heat" },
  { value: "THEAT", label: "THEAT — Cumulative heat" },
];

type Category = 0 | 1 | 2; // 0 Daily, 1 Monthly, 2 Yearly

type ChartMetricKey =
  | "flow"
  | "instantaneousFlow"
  | "instantaneousVelocity"
  | "waterTemperature"
  | "returnWaterTemperature"
  | "accumulatedCooling"
  | "heat";

type MetricInfo = { key: ChartMetricKey; label: string; unit: string };

const METRIC_INFO: Record<ChartMetricKey, MetricInfo> = {
  flow: { key: "flow", label: "Flow", unit: "m³" },
  instantaneousFlow: { key: "instantaneousFlow", label: "Instantaneous Flow", unit: "m³/h" },
  instantaneousVelocity: { key: "instantaneousVelocity", label: "Velocity", unit: "m/s" },
  waterTemperature: { key: "waterTemperature", label: "Inlet Temp", unit: "°C" },
  returnWaterTemperature: { key: "returnWaterTemperature", label: "Return Temp", unit: "°C" },
  accumulatedCooling: { key: "accumulatedCooling", label: "Accumulated Cooling", unit: "kWh" },
  heat: { key: "heat", label: "Heat", unit: "kWh" },
};

function metricForParam(paramName: string): ChartMetricKey {
  // Backend returns multiple fields; we pick the most relevant one per paramName.
  if (paramName === "DQ") return "instantaneousFlow";
  if (paramName === "DV") return "instantaneousVelocity";
  if (paramName === "TI") return "waterTemperature";
  if (paramName === "TO") return "returnWaterTemperature";
  if (paramName === "TH") return "accumulatedCooling";
  if (paramName === "RH" || paramName === "EQH" || paramName === "THEAT") return "heat";
  return "flow";
}

function dateKey(ts: string, category: Category): string {
  // ts expected ISO-ish; keep it simple and consistent for grouping.
  if (category === 0) return ts.slice(0, 10); // YYYY-MM-DD
  if (category === 1) return ts.slice(0, 7); // YYYY-MM
  return ts.slice(0, 4); // YYYY
}

interface Props {
  deviceId: number;
  deviceName: string;
}

export function CumulativeChart({ deviceId, deviceName }: Props) {
  const [paramName, setParamName] = useState("DI+");
  const [category, setCategory] = useState<Category>(0);

  const [data, setData] = useState<CumulativePoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chartMetric = useMemo(() => metricForParam(paramName), [paramName]);
  const currentMetric = METRIC_INFO[chartMetric];

  useEffect(() => {
    setLoading(true);
    setError(null);
    getCumulative(deviceId, { paramName, category })
      .then(setData)
      .catch((e) => {
        setError(
          e?.response?.data?.detail ?? "Failed to load cumulative data."
        );
      })
      .finally(() => setLoading(false));
  }, [category, deviceId, paramName]);

  const option = useMemo(() => {
    const sorted = [...data].sort((a, b) => {
      const ta = (a.issueDate ?? a.fetchedAt) || "";
      const tb = (b.issueDate ?? b.fetchedAt) || "";
      return ta.localeCompare(tb);
    });

    const grouped: Map<string, number | null> = new Map();
    for (const pt of sorted) {
      const ts = pt.issueDate ?? pt.fetchedAt;
      if (!ts) continue;
      const key = dateKey(ts, category);
      const val = pt[chartMetric] ?? null;
      // For cumulative-style series, taking the last reading in each bucket is stable.
      grouped.set(key, val);
    }

    const xData = Array.from(grouped.keys());
    const yData = xData.map((k) => grouped.get(k) ?? null);

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1e293b",
        borderColor: "#334155",
        textStyle: { color: "#e2e8f0" },
        formatter: (params: unknown[]) => {
          const pts = params as { name: string; value: number | null; marker: string }[];
          if (!pts.length) return "";
          return `${pts[0].name}<br/>${pts[0].marker} ${currentMetric.label}: <b>${
            pts[0].value != null ? pts[0].value.toFixed(3) : "—"
          } ${currentMetric.unit}</b>`;
        },
      },
      grid: { top: 16, left: 60, right: 20, bottom: 48 },
      xAxis: {
        type: "category",
        data: xData,
        axisLabel: {
          color: "#94a3b8",
          fontSize: 11,
          rotate: 30,
          formatter: (val: string) => val,
        },
        axisLine: { lineStyle: { color: "#334155" } },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        name: currentMetric.unit,
        nameTextStyle: { color: "#64748b", fontSize: 11 },
        axisLabel: { color: "#94a3b8", fontSize: 11 },
        axisLine: { lineStyle: { color: "#334155" } },
        splitLine: { lineStyle: { color: "#1e293b" } },
      },
      series: [
        {
          name: currentMetric.label,
          type: "bar",
          data: yData,
          itemStyle: { color: "#3b82f6", borderRadius: [3, 3, 0, 0] },
          emphasis: { itemStyle: { color: "#60a5fa" } },
        },
      ],
    };
  }, [category, chartMetric, currentMetric.label, currentMetric.unit, data]);

  return (
    <div className="bg-slate-800/60 border border-slate-700/50 rounded-2xl p-6">
      {/* Header */}
      <div className="flex flex-wrap items-start gap-3 mb-5">
        <div className="flex-1 min-w-0">
          <h2 className="text-white font-semibold truncate">{deviceName}</h2>
          <p className="text-slate-400 text-xs mt-0.5">Cumulative data</p>
        </div>

        {/* Category selector */}
        <div className="relative">
          <select
            value={category}
            onChange={(e) => setCategory(Number(e.target.value) as Category)}
            className="appearance-none bg-slate-700 border border-slate-600 text-white rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value={0}>Daily</option>
            <option value={1}>Monthly</option>
            <option value={2}>Yearly</option>
          </select>
          <div className="pointer-events-none absolute inset-y-0 right-2 flex items-center">
            <svg className="w-3 h-3 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>

        {/* Param selector */}
        <div className="relative">
          <select
            value={paramName}
            onChange={(e) => setParamName(e.target.value)}
            className="appearance-none bg-slate-700 border border-slate-600 text-white rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {PARAM_OPTIONS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          <div className="pointer-events-none absolute inset-y-0 right-2 flex items-center">
            <svg className="w-3 h-3 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>
      </div>

      {/* Chart */}
      {loading ? (
        <div className="h-72 flex items-center justify-center text-slate-400">
          <svg className="animate-spin w-6 h-6 mr-2" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          Fetching from broker…
        </div>
      ) : error ? (
        <div className="h-72 flex items-center justify-center">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      ) : data.length === 0 ? (
        <div className="h-72 flex items-center justify-center text-slate-500 text-sm">
          No data for the selected range and parameter.
        </div>
      ) : (
        <ReactECharts
          option={option}
          style={{ height: 320 }}
          opts={{ renderer: "canvas" }}
        />
      )}

      {/* Row count badge */}
      {!loading && data.length > 0 && (
        <p className="text-xs text-slate-500 mt-3 text-right">
          {data.length} readings
        </p>
      )}
    </div>
  );
}
