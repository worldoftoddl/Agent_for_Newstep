import type { Metadata } from "next";
import "./globals.css";
import React from "react";
import { NuqsAdapter } from "nuqs/adapters/next/app";

export const metadata: Metadata = {
  title: "ExcelBrief for Newsteps",
  description: "신입 회계사를 위한 감사조서(Excel) 해설 에이전트",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
    >
      <body suppressHydrationWarning>
        <NuqsAdapter>{children}</NuqsAdapter>
      </body>
    </html>
  );
}
