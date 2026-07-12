import "./globals.css";

export const metadata = {
  title: "AI Co-Scientist",
  description: "你的 AI 科研合伙人",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
