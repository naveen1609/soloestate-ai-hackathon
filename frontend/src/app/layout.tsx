import type { Metadata } from "next";
import "./globals.css";
import './crm.css';

export const metadata: Metadata = {
  title: "SoloEstate AI",
  description: "An owner-directed operating system for a one-person real estate business.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
