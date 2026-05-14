import type { NextApiRequest, NextApiResponse } from 'next';

const COOKIE_NAME    = 'stockai_gate';
const COOKIE_MAX_AGE = 60 * 60 * 24 * 7; // 7 days

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  const sitePassword = process.env.SITE_PASSWORD || '';

  // GET — let the client know whether the gate is active (server-side only)
  if (req.method === 'GET') {
    return res.status(200).json({ enabled: sitePassword.length > 0 });
  }

  if (req.method !== 'POST') return res.status(405).end();

  const { password } = req.body as { password?: string };

  if (!sitePassword || !password || password !== sitePassword) {
    return res.status(401).json({ error: 'Incorrect password' });
  }

  res.setHeader(
    'Set-Cookie',
    `${COOKIE_NAME}=1; Path=/; Max-Age=${COOKIE_MAX_AGE}; SameSite=Lax`,
  );
  res.status(200).json({ ok: true });
}
