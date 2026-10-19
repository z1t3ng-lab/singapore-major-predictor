import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Major Forecast · PGL Singapore 2026",
  description: "Explainable CS2 Major match predictions, Swiss simulation and a projected Singapore playoff bracket.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
