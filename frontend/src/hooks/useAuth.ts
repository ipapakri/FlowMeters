import { useCallback, useEffect, useState } from "react";
import { getMe, login as apiLogin, logout as apiLogout } from "../api";

export function useAuth() {
  const [username, setUsername] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMe()
      .then((data) => setUsername(data.username))
      .catch(() => setUsername(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (user: string, pass: string) => {
    const data = await apiLogin(user, pass);
    setUsername(data.username);
    return data;
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUsername(null);
  }, []);

  return { username, loading, login, logout };
}
