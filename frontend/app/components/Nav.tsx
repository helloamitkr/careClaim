"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Cases" },
  { href: "/stats", label: "Stats" },
  { href: "/logs", label: "Logs" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="ml-auto flex items-center gap-1 text-sm">
      {LINKS.map((link) => {
        const active =
          link.href === "/" ? pathname === "/" || pathname.startsWith("/cases") : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            className={`rounded-full px-3 py-1.5 transition-colors ${
              active
                ? "bg-teal-600/10 text-teal-700 dark:bg-teal-400/10 dark:text-teal-300 font-medium"
                : "text-black/55 dark:text-white/55 hover:text-black dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
