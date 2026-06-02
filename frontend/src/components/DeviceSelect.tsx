import type { Device } from "../api";

interface Props {
  devices: Device[];
  selected: "all" | number;
  onChange: (value: "all" | number) => void;
  loading?: boolean;
}

export function DeviceSelect({ devices, selected, onChange, loading }: Props) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <label className="hidden sm:block text-sm text-slate-400 whitespace-nowrap">
        Device
      </label>
      <div className="relative flex-1 min-w-0 sm:flex-none sm:min-w-[180px]">
        <select
          value={selected === "all" ? "all" : String(selected)}
          onChange={(e) =>
            onChange(e.target.value === "all" ? "all" : Number(e.target.value))
          }
          disabled={loading}
          className="appearance-none bg-slate-800 border border-slate-600 text-white rounded-lg pl-3 pr-8 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 cursor-pointer w-full min-w-[8rem] sm:w-auto sm:min-w-[180px]"
        >
          <option value="all">All devices</option>
          {devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name || `Device ${d.id}`}
              {d.serialNo ? ` (${d.serialNo})` : ""}
            </option>
          ))}
        </select>
        {/* chevron */}
        <div className="pointer-events-none absolute inset-y-0 right-2 flex items-center">
          <svg
            className="w-4 h-4 text-slate-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M19 9l-7 7-7-7"
            />
          </svg>
        </div>
      </div>
      {loading && (
        <svg
          className="animate-spin w-4 h-4 text-blue-400"
          viewBox="0 0 24 24"
          fill="none"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8v8H4z"
          />
        </svg>
      )}
    </div>
  );
}
