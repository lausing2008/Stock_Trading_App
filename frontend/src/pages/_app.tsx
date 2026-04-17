import type { AppProps } from 'next/app';
import Link from 'next/link';
import '@/styles/globals.css';

export default function App({ Component, pageProps }: AppProps) {
  return (
    <div>
      <header className="border border-slate-800 bg-slate-900">
        <div className="container-xl flex items-center justify-between">
          <Link href="/" className="text-lg font-bold">
            <span style={{ color: '#818cf8' }}>Stock</span>AI
          </Link>
          <nav className="flex gap-4 text-sm text-slate-300">
            <Link href="/">Dashboard</Link>
            <Link href="/rankings">Rankings</Link>
            <Link href="/portfolio">Portfolio</Link>
            <Link href="/strategies">Strategies</Link>
          </nav>
        </div>
      </header>
      <main className="container-xl">
        <Component {...pageProps} />
      </main>
    </div>
  );
}
