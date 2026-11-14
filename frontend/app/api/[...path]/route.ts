// Stream request and response bodies. Never call formData(), text(), or arrayBuffer()
// here: a full assessment CSV must not be buffered by the frontend process.
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
type Context = {params: Promise<{path: string[]}>};
async function forward(request: Request, context: Context) {
  const {path} = await context.params;
  const incoming = new URL(request.url);
  const target = new URL('/api/' + path.map(encodeURIComponent).join('/') + '/', process.env.BACKEND_URL || 'http://127.0.0.1:8000');
  target.search = incoming.search;
  const headers = new Headers(request.headers);
  for (const name of ['host', 'connection', 'transfer-encoding', 'upgrade', 'expect']) headers.delete(name);
  headers.set('accept-encoding', 'identity');
  try {
    const options: RequestInit & {duplex: 'half'} = {
      method: request.method, headers, body: request.body,
      duplex: 'half', redirect: 'manual', cache: 'no-store', signal: request.signal,
    };
    const upstream = await fetch(target, options);
    const responseHeaders = new Headers(upstream.headers);
    for (const name of ['connection', 'transfer-encoding', 'content-encoding']) responseHeaders.delete(name);
    responseHeaders.set('cache-control', 'no-store');
    return new Response(upstream.body, {status: upstream.status, headers: responseHeaders});
  } catch {
    return Response.json({error: 'Cannot reach Django. Start the backend on port 8000 and check BACKEND_URL.'}, {status: 502});
  }
}
export {forward as GET, forward as POST, forward as PATCH, forward as DELETE, forward as HEAD};
