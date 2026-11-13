/** Keys for other services (CONTRACT.md): listed, saved and removed; a key is never shown. */

export interface ServiceKey {
  name: string;
  label: string;
  used_for: string;
  env: string;
  site: string;
  configured: boolean;
  source: "env" | "store" | null;
}

/** Where a service's key is, in words. */
export function keyState(service: ServiceKey): string {
  if (service.source === "env") return `A key is set on this computer, in the variable ${service.env}. The studio uses it and did not save it.`;
  if (service.source === "store") return "A key is saved in this computer's keychain or the studio's owner-only file.";
  return `No key. Save one here, or set the variable ${service.env} before starting the studio.`;
}

/** A key in the environment is not the studio's to remove; one it saved is. */
export function canRemove(service: ServiceKey): boolean {
  return service.source === "store";
}
