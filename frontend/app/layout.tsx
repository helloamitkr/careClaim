import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Nav } from "@/app/components/Nav";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "CareBridge AI — Workbench",
  description: "Care manager workbench for CareBridge AI transition-of-care cases",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <header className="sticky top-0 z-40 border-b border-black/10 dark:border-white/10 bg-[var(--background)]/80 backdrop-blur">
          <div className="mx-auto max-w-5xl px-6 py-3 flex items-center gap-3">
            <Link href="/" className="flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-teal-500 to-teal-700 text-white shadow-sm">
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path
                    d="M3 12h4l2.5-6 4 12L16 12h5"
                    stroke="currentColor"
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
              <span className="text-lg font-semibold tracking-tight">
                CareBridge <span className="text-teal-600 dark:text-teal-400">AI</span>
              </span>
            </Link>
            <span className="hidden sm:inline mt-0.5 text-[11px] uppercase tracking-wider text-black/35 dark:text-white/35">
              Care Manager Workbench
            </span>
            <Nav />
          </div>
        </header>
        <main className="flex-1">{children}</main>
        <footer className="border-t border-black/5 dark:border-white/5">
          <div className="mx-auto max-w-5xl px-6 py-4 text-[11px] text-black/35 dark:text-white/35">
            5 agents · confidence-routed · human-in-the-loop · every decision audited
          </div>
        </footer>
      </body>
    </html>
  );
}
