"use client";

import { usePathname } from "next/navigation";

/**
 * The patient portal is a separate trust zone and must not render the staff
 * workbench header, nav, or footer — a patient should never see a link to the
 * case queue, and shared chrome is how internal surfaces leak into patient UI.
 */
export function StaffChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (pathname?.startsWith("/patient")) return null;
  return <>{children}</>;
}
