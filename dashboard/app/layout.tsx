import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ThetaForge Personal Terminal",
  description: "A personal options intelligence dashboard for an IBKR workflow.",
  openGraph: {
    title: "ThetaForge Personal Terminal",
    description: "Personal options intelligence for an IBKR workflow.",
    images: ["/og.png"],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
