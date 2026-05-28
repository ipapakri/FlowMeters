import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { useAuth } from "./hooks/useAuth";

export default function App() {
  const { username, loading, login, logout } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <svg
          className="animate-spin w-8 h-8 text-blue-400"
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
      </div>
    );
  }

  if (!username) {
    return <LoginPage onLogin={login} />;
  }

  return <DashboardPage username={username} onLogout={logout} />;
}
