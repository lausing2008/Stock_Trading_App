import type { NextApiRequest, NextApiResponse } from 'next';

// Research generation calls Claude, which can take 60-90 seconds.
// Next.js 14 rewrites have a short socket idle timeout that drops these requests.
// This API route replaces the rewrite for /api/research/* and extends the socket
// timeout to 150s so long-running Claude calls complete successfully.

export const config = {
  api: {
    bodyParser: false,
    responseLimit: false,
    externalResolver: true,
  },
};

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  // Extend socket timeout beyond the default rewrite proxy idle timeout
  if (req.socket) req.socket.setTimeout(150_000);

  const { symbol } = req.query;
  const upstream = process.env.API_GATEWAY_URL || 'http://api-gateway:8000';
  const url = `${upstream}/research/${symbol}`;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 150_000);

  try {
    // Buffer the request body for POST/DELETE
    let body: Buffer | undefined;
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      body = await new Promise<Buffer>((resolve, reject) => {
        const chunks: Buffer[] = [];
        req.on('data', (chunk: Buffer) => chunks.push(chunk));
        req.on('end', () => resolve(Buffer.concat(chunks)));
        req.on('error', reject);
      });
    }

    // Forward headers, replacing host with the upstream host
    const headers: Record<string, string> = {};
    for (const [k, v] of Object.entries(req.headers)) {
      if (k === 'host') continue;
      if (k === 'content-length') continue;
      if (v) headers[k] = Array.isArray(v) ? v[0] : v;
    }

    const r = await fetch(url, {
      method: req.method,
      headers,
      body: body?.length ? body : undefined,
      signal: controller.signal,
    });

    res.status(r.status);
    const ct = r.headers.get('content-type');
    if (ct) res.setHeader('Content-Type', ct);

    const text = await r.text();
    res.end(text);
  } catch (e: unknown) {
    if (!res.headersSent) {
      const msg = e instanceof Error && e.name === 'AbortError'
        ? 'Research request timed out (150s limit). Try again — the result may be cached.'
        : `Upstream error: ${e instanceof Error ? e.message : String(e)}`;
      res.status(504).json({ detail: msg });
    }
  } finally {
    clearTimeout(timeoutId);
  }
}
