import "./globals.css";

export const metadata = {
  title: "Research Forge",
  description: "Evidence-gated research reproduction control plane.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
