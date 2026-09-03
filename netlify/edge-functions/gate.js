// Site-wide password gate.
//
// Why an edge function instead of Netlify's built-in "Visitor access" password: that feature
// is Pro-plan and dashboard-only, and this repo deploys from git on a personal account. This
// runs on every request (path "/*"), so it protects data.json — where the copyrighted APA
// rules actually live — not just the HTML. The shared secret is intentionally weak; the point
// is to keep the members-only tool (and © APA text) off the open, indexable web, not to defend
// against a determined attacker.
const PASSWORD = "pool";
const COOKIE = "rs_gate";

function page(wrong) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Rack Sheet</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0e141b;
    color:#e7edf2;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  .card{width:min(360px,92vw);background:#161f28;border:1px solid #26333f;border-radius:16px;
    padding:26px 24px;box-shadow:0 10px 40px rgba(0,0,0,.4);text-align:center}
  .mark{width:34px;height:34px;margin:0 auto 14px;transform:rotate(45deg);border-radius:7px;
    background:linear-gradient(135deg,#4bb6c9,#1f7a8c)}
  h1{font-size:19px;letter-spacing:.06em;text-transform:uppercase;margin:0 0 4px}
  p{color:#93a4b3;font-size:13px;margin:0 0 18px}
  input{width:100%;padding:12px 14px;font-size:16px;border-radius:10px;border:1px solid #26333f;
    background:#0e141b;color:#e7edf2;text-align:center;letter-spacing:.08em}
  input:focus{outline:2px solid #4bb6c9;border-color:transparent}
  button{width:100%;margin-top:12px;padding:12px;font-size:14px;font-weight:700;letter-spacing:.06em;
    text-transform:uppercase;border:0;border-radius:10px;cursor:pointer;color:#04222a;
    background:linear-gradient(135deg,#4bb6c9,#1f7a8c)}
  .err{color:#e0715f;font-size:12.5px;margin-top:12px;min-height:16px}
</style></head><body>
  <form class="card" method="POST" autocomplete="off">
    <div class="mark"></div>
    <h1>Rack Sheet</h1>
    <p>Team tool — enter the password to continue.</p>
    <input name="password" type="password" placeholder="Password" autofocus aria-label="Password">
    <button type="submit">Enter</button>
    <div class="err">${wrong ? "That password didn't work — try again." : ""}</div>
  </form>
</body></html>`;
}

export default async (request, context) => {
  const cookie = request.headers.get("cookie") || "";
  if (cookie.split(/;\s*/).includes(`${COOKIE}=ok`)) return context.next();

  if (request.method === "POST") {
    const form = await request.formData().catch(() => null);
    if (form && (form.get("password") || "") === PASSWORD) {
      return new Response("", {
        status: 303,
        headers: {
          "location": "/",
          "set-cookie": `${COOKIE}=ok; Path=/; Max-Age=7776000; SameSite=Lax; Secure; HttpOnly`,
        },
      });
    }
    return new Response(page(true), { status: 401, headers: { "content-type": "text/html; charset=utf-8" } });
  }

  return new Response(page(false), { status: 401, headers: { "content-type": "text/html; charset=utf-8" } });
};

export const config = { path: "/*" };
