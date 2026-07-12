import "./globals.css";

export const metadata = {
  title: "Research Forge",
  description: "Explore research directions, then verify reproducible claims with evidence.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
