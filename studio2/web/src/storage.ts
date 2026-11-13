// localStorage that never throws (private mode, quota, disabled storage).

const PREFIX = "studio2:";

export function loadString(key: string): string | null {
  try {
    return localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

export function saveString(key: string, value: string | null): void {
  try {
    if (value === null || value === "") localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, value);
  } catch {
    /* storage unavailable: the preference simply does not persist */
  }
}
